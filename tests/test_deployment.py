from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import ConfigError, Settings
from app.main import create_app
from app.web.deployment import public_origin

PUBLIC = "https://accounts.example"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://ACCOUNTS.example/", PUBLIC),
        ("https://accounts.example:443", PUBLIC),
        ("https://accounts.example:8443", PUBLIC + ":8443"),
        ("https://127.0.0.1", "https://127.0.0.1"),
        ("https://[::1]:443/", "https://[::1]"),
    ],
)
def test_public_origin_normalizes_https_authority(value: str, expected: str) -> None:
    assert public_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://accounts.example",
        "https://",
        "//accounts.example",
        "https://u:p@x.example",
        "https://accounts.example/path",
        "https://accounts.example?",
        "https://accounts.example#",
        "https://accounts.example:0",
        "https://accounts.example:65536",
        "https://accounts.example:",
        "https://accounts.example\n",
        "https://accounts.example\\",
        "https://a..example",
        "https://-a.example",
        "https://a-.example",
        "https://a_b.example",
        "https://[broken",
    ],
)
def test_invalid_public_url_is_rejected_without_echoing_it(value: str) -> None:
    with pytest.raises(ConfigError, match="KW_PUBLIC_URL") as exc:
        Settings.from_env({"KW_PUBLIC_URL": value})
    assert value not in str(exc.value)


def test_public_url_and_insecure_cookie_cannot_be_combined() -> None:
    with pytest.raises(ConfigError, match="KW_COOKIE_SECURE"):
        Settings.from_env({"KW_PUBLIC_URL": PUBLIC, "KW_COOKIE_SECURE": "0"})
    assert Settings.from_env({"KW_PUBLIC_URL": PUBLIC}).public_url == PUBLIC


def test_https_login_works_with_hardened_cookies(settings: Settings) -> None:
    cfg = replace(settings, public_url=PUBLIC)
    with TestClient(create_app(cfg), base_url=PUBLIC) as client:
        response = client.post(
            "/login",
            data={"username": "Admin", "password": "Passwort123"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "Secure" in response.headers["set-cookie"]
        assert response.headers["strict-transport-security"] == "max-age=31536000"


def test_http_and_foreign_hosts_never_receive_the_login(settings: Settings) -> None:
    cfg = replace(settings, public_url=PUBLIC)
    with TestClient(create_app(cfg), base_url="http://accounts.example") as client:
        for path in ("/login", "/", "/static/style.css"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 400
            assert "set-cookie" not in response.headers
            assert "x-frame-options" in response.headers
        assert client.get("/health").status_code == 200
        assert client.get(PUBLIC + "/login", headers={"host": "foreign.example"}).status_code == 400
        assert client.get(PUBLIC + "/login", headers={"host": "[broken"}).status_code == 400


def test_forwarded_headers_require_a_trusted_proxy(settings: Settings) -> None:
    cfg = replace(settings, public_url=PUBLIC)
    for trusted, status in (("127.0.0.1", 400), ("testclient", 200)):
        # Uvicorn und Starlette verwenden unterschiedliche, laufzeitkompatible ASGI-Protokolltypen.
        wrapped = ProxyHeadersMiddleware(create_app(cfg), trusted_hosts=trusted)  # type: ignore[arg-type]
        with TestClient(wrapped, base_url="http://accounts.example") as client:  # type: ignore[arg-type]
            response = client.get("/login", headers={"x-forwarded-proto": "https"})
            assert response.status_code == status


def test_csrf_does_not_accept_the_http_origin_of_the_https_site(settings: Settings) -> None:
    cfg = replace(settings, public_url=PUBLIC)
    with TestClient(create_app(cfg), base_url=PUBLIC) as client:
        response = client.post(
            "/login",
            data={"username": "Admin", "password": "Passwort123"},
            headers={"origin": "http://accounts.example"},
            follow_redirects=False,
        )
        assert response.status_code == 403
