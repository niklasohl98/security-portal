"""Entra- und AD-Konten eines Mandanten zusammenführen.

Reihenfolge der Zuordnung (jede Stufe sieht nur Konten, die vorher keinen Partner fanden):

1. ``onPremisesImmutableId`` (Base64 der 16 ``objectGUID``-Bytes) = ``objectGUID`` des AD-Kontos
2. ``onPremisesSamAccountName`` = ``sAMAccountName`` (je Mandant gibt es eine AD-Domäne)
3. UPN gleich (Groß-/Kleinschreibung egal)

Ein Schlüssel, der in einer Stufe mehr als ein Konto trifft, ist mehrdeutig und ordnet nichts zu;
solche Konten bleiben allein stehen und werden in ``MergeResult.ambiguous`` gezählt. Die Funktionen
lesen nur und kennen weder Datenbank noch Uhr.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.db.models import AdAccount, EntraAccount


class MatchBy(StrEnum):
    """Womit ein Entra-Konto einem AD-Konto zugeordnet wurde."""

    IMMUTABLE_ID = "immutable_id"
    SAM_ACCOUNT_NAME = "sam_account_name"
    UPN = "upn"


@dataclass(frozen=True)
class Pair:
    """Ein zusammengeführtes Konto. Mindestens eine Seite ist gesetzt."""

    entra: EntraAccount | None
    ad: AdAccount | None
    matched_by: MatchBy | None = None


@dataclass
class MergeResult:
    """Ergebnis von ``merge_accounts``."""

    pairs: list[Pair] = field(default_factory=list)
    ambiguous: int = 0  # Konten, deren Schlüssel in einer Stufe mehrere Treffer hatte


def immutable_id_to_guid(immutable_id: str | None) -> str | None:
    """Wandelt ``onPremisesImmutableId`` in die GUID-Schreibweise des AD (kleingeschrieben).

    Entra Connect legt die 16 Rohbytes von ``objectGUID`` (Windows-Reihenfolge) Base64-kodiert
    ab. Andere Werte (selbst gewählter Quellanker, kein Base64, falsche Länge) ergeben ``None``.
    """
    if not immutable_id:
        return None
    try:
        raw = base64.b64decode(immutable_id, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) != 16:
        return None
    return str(uuid.UUID(bytes_le=raw))


def _fold(value: str | None) -> str | None:
    return value.strip().casefold() or None if value else None


def _stage(
    entra: Sequence[EntraAccount],
    ad: Sequence[AdAccount],
    entra_key: Callable[[EntraAccount], str | None],
    ad_key: Callable[[AdAccount], str | None],
) -> tuple[list[tuple[EntraAccount, AdAccount]], set[int]]:
    """Eine Zuordnungsstufe: Paare mit genau einem Treffer auf beiden Seiten.

    Zweiter Rückgabewert: ``id()`` der Entra-Konten mit mehrdeutigem Schlüssel.
    """
    by_ad: dict[str, list[AdAccount]] = defaultdict(list)
    for a in ad:
        if (k := ad_key(a)) is not None:
            by_ad[k].append(a)
    by_entra: dict[str, list[EntraAccount]] = defaultdict(list)
    for e in entra:
        if (k := entra_key(e)) is not None:
            by_entra[k].append(e)

    pairs: list[tuple[EntraAccount, AdAccount]] = []
    ambiguous: set[int] = set()
    for key, entra_hits in by_entra.items():
        ad_hits = by_ad.get(key)
        if not ad_hits:
            continue
        if len(entra_hits) == 1 and len(ad_hits) == 1:
            pairs.append((entra_hits[0], ad_hits[0]))
        else:
            ambiguous.update(id(e) for e in entra_hits)
    return pairs, ambiguous


_STAGES: tuple[
    tuple[MatchBy, Callable[[EntraAccount], str | None], Callable[[AdAccount], str | None]], ...
] = (
    (
        MatchBy.IMMUTABLE_ID,
        lambda e: immutable_id_to_guid(e.on_prem_immutable_id),
        lambda a: _fold(a.object_guid),
    ),
    (
        MatchBy.SAM_ACCOUNT_NAME,
        lambda e: _fold(e.on_prem_sam_account_name),
        lambda a: _fold(a.sam_account_name),
    ),
    (MatchBy.UPN, lambda e: _fold(e.user_principal_name), lambda a: _fold(a.user_principal_name)),
)


def merge_accounts(
    entra: Iterable[EntraAccount], ad: Iterable[AdAccount], *, ad_available: bool = True
) -> MergeResult:
    """Führt die Konten eines Mandanten zusammen.

    Mit ``ad_available=False`` (Mandant ohne AD oder noch kein AD-Lauf) bleibt jedes Entra-Konto
    allein; die Stufen laufen dann gar nicht erst. Die Reihenfolge des Ergebnisses ist
    deterministisch: erst alle Entra-Konten in Eingabereihenfolge, dann die AD-Konten ohne Partner.
    """
    entra_all = list(entra)
    ad_all = list(ad) if ad_available else []
    result = MergeResult()
    ambiguous_ids: set[int] = set()

    matched: dict[int, tuple[AdAccount, MatchBy]] = {}  # id(entra) -> (ad, Stufe)
    used_ad: set[int] = set()
    open_entra = entra_all
    open_ad = ad_all
    for stage, entra_key, ad_key in _STAGES:
        found, ambiguous = _stage(open_entra, open_ad, entra_key, ad_key)
        ambiguous_ids |= ambiguous
        for e, a in found:
            matched[id(e)] = (a, stage)
            used_ad.add(id(a))
        open_entra = [e for e in open_entra if id(e) not in matched]
        open_ad = [a for a in open_ad if id(a) not in used_ad]

    result.ambiguous = len(ambiguous_ids - matched.keys())
    for e in entra_all:
        hit = matched.get(id(e))
        result.pairs.append(Pair(e, hit[0], hit[1]) if hit else Pair(e, None))
    result.pairs.extend(Pair(None, a) for a in ad_all if id(a) not in used_ad)
    return result
