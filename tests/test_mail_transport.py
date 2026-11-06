"""Mailversand über Graph: Anfrage, Fehlerabbildung ohne Leaks, Token-Quelle."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.connectors.graph import GraphError
from app.mail import transport
from app.mail.settings import MailSettings
from app.mail.transport import (
    MailError,
    build_message,
    check_connection,
    make_token_provider,
    send_message,
)

SETTINGS = MailSettings(
    tenant_id="00000000-0000-4000-8000-0000000000a1",
    client_id="00000000-0000-4000-8000-0000000000a2",
    certificate_secret="mail_cert",
    certificate_thumbprint="0123456789ABCDEF0123456789ABCDEF01234567",
    sender="bericht@beispiel.test",
)
TOKEN = "token-abc-123"


class FakeTokens:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def get_token(self) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return TOKEN


def _mail() -> transport.OutgoingMail:
    return build_message(["a@beispiel.test", "b@beispiel.test"], "Betreff", "Text")


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_the_request_goes_to_the_sender_mailbox_with_a_text_body(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    send_message(
        SETTINGS, tmp_path, _mail(), tokens=FakeTokens(), http=_client(httpx.MockTransport(handle))
    )
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://graph.microsoft.com/v1.0/users/bericht@beispiel.test/sendMail"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(req.content)
    assert body["saveToSentItems"] is False
    msg = body["message"]
    assert msg["subject"] == "Betreff"
    assert msg["body"] == {"contentType": "Text", "content": "Text"}
    assert [r["emailAddress"]["address"] for r in msg["toRecipients"]] == [
        "a@beispiel.test",
        "b@beispiel.test",
    ]


def test_the_sender_is_url_quoted(tmp_path: Path) -> None:
    from dataclasses import replace

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    cfg = replace(SETTINGS, sender="a b/../c@beispiel.test")
    send_message(
        cfg, tmp_path, _mail(), tokens=FakeTokens(), http=_client(httpx.MockTransport(handle))
    )
    assert seen[0].url.raw_path == b"/v1.0/users/a%20b%2F..%2Fc@beispiel.test/sendMail"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "rejected"),
        (401, "auth"),
        (403, "forbidden"),
        (404, "sender"),
        (429, "throttled"),
        (500, "send"),
        (503, "send"),
    ],
)
def test_http_errors_become_fixed_codes_and_keep_only_the_graph_code(
    tmp_path: Path, status: int, code: str
) -> None:
    body = {"error": {"code": "ErrorAccessDenied", "message": "Zugriff für a@beispiel.test"}}
    handler = httpx.MockTransport(lambda _r: httpx.Response(status, json=body))
    with pytest.raises(MailError) as err:
        send_message(SETTINGS, tmp_path, _mail(), tokens=FakeTokens(), http=_client(handler))
    assert err.value.code == code
    assert err.value.graph_code == "ErrorAccessDenied"
    text = str(err.value) + repr(err.value)
    assert "beispiel.test" not in text
    assert TOKEN not in text


def test_hostile_or_missing_graph_codes_are_dropped(tmp_path: Path) -> None:
    for payload in (
        httpx.Response(403, json={"error": {"code": "x y\nz"}}),
        httpx.Response(403, json={"error": {"code": "A" * 101}}),
        httpx.Response(403, json={"error": "text"}),
        httpx.Response(403, text="kein json"),
    ):
        handler = httpx.MockTransport(lambda _r, p=payload: p)
        with pytest.raises(MailError) as err:
            send_message(SETTINGS, tmp_path, _mail(), tokens=FakeTokens(), http=_client(handler))
        assert (err.value.code, err.value.graph_code) == ("forbidden", None)


def test_network_errors_are_connect_and_never_retried(tmp_path: Path) -> None:
    calls = 0

    def boom(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("a@beispiel.test nicht erreichbar", request=request)

    with pytest.raises(MailError) as err:
        send_message(
            SETTINGS,
            tmp_path,
            _mail(),
            tokens=FakeTokens(),
            http=_client(httpx.MockTransport(boom)),
        )
    assert err.value.code == "connect"
    assert err.value.__cause__ is None
    assert calls == 1  # sendMail ist nicht idempotent: keine automatische Wiederholung


def test_a_throttled_request_is_not_retried_either(tmp_path: Path) -> None:
    calls = 0

    def handle(_r: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "1"})

    with pytest.raises(MailError):
        send_message(
            SETTINGS,
            tmp_path,
            _mail(),
            tokens=FakeTokens(),
            http=_client(httpx.MockTransport(handle)),
        )
    assert calls == 1


def test_token_failures_stop_before_any_request(tmp_path: Path) -> None:
    def never(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("darf nicht senden")

    for error, code in (
        (GraphError(code="auth:invalid_client"), "auth"),
        (httpx.ConnectError("weg"), "connect"),
    ):
        with pytest.raises(MailError) as err:
            send_message(
                SETTINGS,
                tmp_path,
                _mail(),
                tokens=FakeTokens(error),
                http=_client(httpx.MockTransport(never)),
            )
        assert err.value.code == code


def test_check_connection_only_fetches_a_token(tmp_path: Path) -> None:
    tokens = FakeTokens()
    check_connection(SETTINGS, tmp_path, tokens=tokens)
    assert tokens.calls == 1
    with pytest.raises(MailError) as err:
        check_connection(SETTINGS, tmp_path, tokens=FakeTokens(GraphError(code="auth")))
    assert err.value.code == "auth"


def test_the_token_provider_uses_the_secret_and_the_mail_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mail_cert").write_text("PEM-INHALT\n", encoding="utf-8")
    seen: dict[str, object] = {}

    class FakeProvider:
        def __init__(self, tenant: str, client: str, pem: str, thumb: str) -> None:
            seen.update(tenant=tenant, client=client, pem=pem, thumb=thumb)

    monkeypatch.setattr(transport, "MsalCertificateTokenProvider", FakeProvider)
    make_token_provider(SETTINGS, tmp_path)
    assert seen == {
        "tenant": SETTINGS.tenant_id,
        "client": SETTINGS.client_id,
        "pem": "PEM-INHALT",
        "thumb": SETTINGS.certificate_thumbprint,
    }


def test_provider_errors_are_coded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(MailError) as missing:
        make_token_provider(SETTINGS, tmp_path)
    assert missing.value.code == "secret"
    (tmp_path / "mail_cert").write_text("kaputt", encoding="utf-8")

    def bad(*_a: object) -> None:
        raise ValueError("Schlüssel ungültig")

    monkeypatch.setattr(transport, "MsalCertificateTokenProvider", bad)
    with pytest.raises(MailError) as invalid:
        make_token_provider(SETTINGS, tmp_path)
    assert invalid.value.code == "auth"
    assert invalid.value.__cause__ is None


def test_the_subject_loses_line_breaks() -> None:
    mail = build_message(["a@beispiel.test"], "Hallo\r\nBcc: boese@beispiel.test", "Text")
    assert "\n" not in mail.subject
    assert "\r" not in mail.subject
