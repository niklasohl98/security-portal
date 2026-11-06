from __future__ import annotations

import base64
import uuid

from app.core.merge import MatchBy, immutable_id_to_guid, merge_accounts
from app.db.models import AdAccount, EntraAccount

GUID_1 = "12345678-9abc-def0-1234-56789abcdef0"
GUID_2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _immutable(guid: str) -> str:
    """So legt Entra Connect die objectGUID ab: Base64 der 16 Rohbytes (Windows-Reihenfolge)."""
    return base64.b64encode(uuid.UUID(guid).bytes_le).decode()


def _entra(n: int, **kw: object) -> EntraAccount:
    return EntraAccount(
        object_id=f"e{n:07d}-0000-4000-8000-000000000000",
        user_principal_name=f"user{n}@beispiel.test",
        **kw,
    )


def _ad(n: int, **kw: object) -> AdAccount:
    return AdAccount(object_guid=f"{n:08d}-0000-4000-8000-000000000000", **kw)


def test_immutable_id_is_converted_to_the_ad_guid_notation() -> None:
    assert immutable_id_to_guid(_immutable(GUID_1)) == GUID_1
    # Die Byte-Reihenfolge zählt: Base64 der Textform ergäbe eine andere GUID.
    wrong = base64.b64encode(uuid.UUID(GUID_1).bytes).decode()
    assert immutable_id_to_guid(wrong) != GUID_1


def test_invalid_immutable_ids_give_none() -> None:
    assert immutable_id_to_guid(None) is None
    assert immutable_id_to_guid("") is None
    assert immutable_id_to_guid("kein base64!") is None
    assert immutable_id_to_guid(base64.b64encode(b"zu kurz").decode()) is None


def test_matches_by_immutable_id_then_sam_then_upn() -> None:
    by_guid = _entra(1, on_prem_immutable_id=_immutable(GUID_1))
    by_sam = _entra(2, on_prem_sam_account_name="Meier.Test")
    by_upn = _entra(3)
    alone = _entra(4)
    ad_guid = AdAccount(
        object_guid=GUID_1, sam_account_name="anders", user_principal_name="x@y.test"
    )
    ad_sam = AdAccount(object_guid=GUID_2, sam_account_name="meier.test")
    ad_upn = _ad(3, user_principal_name="USER3@Beispiel.Test")
    ad_alone = _ad(9, sam_account_name="nur.ad")

    result = merge_accounts([by_guid, by_sam, by_upn, alone], [ad_guid, ad_sam, ad_upn, ad_alone])

    got = [(p.entra, p.ad, p.matched_by) for p in result.pairs]
    assert got == [
        (by_guid, ad_guid, MatchBy.IMMUTABLE_ID),
        (by_sam, ad_sam, MatchBy.SAM_ACCOUNT_NAME),
        (by_upn, ad_upn, MatchBy.UPN),
        (alone, None, None),
        (None, ad_alone, None),
    ]
    assert result.ambiguous == 0


def test_stronger_stage_wins_over_a_weaker_one() -> None:
    # Gleicher UPN wie ein anderes AD-Konto, aber die GUID gehört dem ersten.
    entra = _entra(1, on_prem_immutable_id=_immutable(GUID_1))
    right = AdAccount(object_guid=GUID_1, user_principal_name="nope@beispiel.test")
    decoy = AdAccount(object_guid=GUID_2, user_principal_name="user1@beispiel.test")

    pairs = merge_accounts([entra], [right, decoy]).pairs

    assert pairs[0].ad is right
    assert pairs[1].ad is decoy
    assert pairs[1].entra is None


def test_ambiguous_key_matches_nothing_and_is_counted_once() -> None:
    e1, e2 = (
        _entra(1, on_prem_sam_account_name="gleich"),
        _entra(2, on_prem_sam_account_name="gleich"),
    )
    ad = _ad(1, sam_account_name="gleich", user_principal_name="user1@beispiel.test")

    result = merge_accounts([e1, e2], [ad])

    # e1 findet danach über den UPN doch noch seinen Partner (eindeutig), e2 bleibt allein.
    assert [(p.entra, p.ad is not None) for p in result.pairs] == [(e1, True), (e2, False)]
    assert result.pairs[0].matched_by is MatchBy.UPN
    assert result.ambiguous == 1  # e2


def test_duplicate_upns_in_ad_are_not_guessed() -> None:
    entra = _entra(1)
    a1 = _ad(1, user_principal_name="user1@beispiel.test")
    a2 = _ad(2, user_principal_name="user1@beispiel.test")

    result = merge_accounts([entra], [a1, a2])

    assert [p.ad for p in result.pairs] == [None, a1, a2]
    assert result.ambiguous == 1


def test_without_ad_data_every_entra_account_stands_alone() -> None:
    entra = _entra(1, on_prem_sam_account_name="x")
    ad = _ad(1, sam_account_name="x")

    result = merge_accounts([entra], [ad], ad_available=False)

    assert [(p.entra, p.ad) for p in result.pairs] == [(entra, None)]


def test_blank_keys_never_match() -> None:
    entra = _entra(1, on_prem_sam_account_name="  ")
    ad = _ad(1, sam_account_name="")

    assert [p.ad for p in merge_accounts([entra], [ad]).pairs] == [None, ad]
