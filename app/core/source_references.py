"""Stabile Referenzen für neu beobachtete Entra-Konten erfassen."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DirectorySource, Source, SourceAccount


def _uuid(value: str) -> str | None:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        return None


def record_entra_accounts(
    session: Session, tenant_id: int, directory_id: str, object_ids: Iterable[str]
) -> bool:
    """Erfasst gültige Objekt-IDs idempotent innerhalb der Transaktion des Abrufs.

    Ungültige IDs werden nicht als stabile Referenz behauptet. Es werden keine bestehenden
    Identitäten zugeordnet oder historische Verzeichniszugehörigkeiten angenommen. Gibt ``False``
    zurück, wenn eine Verzeichnis- oder Objekt-ID nicht als UUID lesbar ist.
    """
    directory_key = _uuid(directory_id)
    keys = [_uuid(value) for value in object_ids]
    account_keys = {key for key in keys if key is not None}
    if directory_key is None:
        return False
    source = session.scalars(
        select(DirectorySource).where(
            DirectorySource.tenant_id == tenant_id,
            DirectorySource.source == Source.ENTRA,
            DirectorySource.external_id == directory_key,
        )
    ).one_or_none()
    if source is None:
        source = DirectorySource(
            tenant_id=tenant_id, source=Source.ENTRA, external_id=directory_key
        )
        session.add(source)
        session.flush()
    existing = set(
        session.scalars(
            select(SourceAccount.external_id).where(
                SourceAccount.tenant_id == tenant_id,
                SourceAccount.directory_source_id == source.id,
            )
        )
    )
    session.add_all(
        SourceAccount(tenant_id=tenant_id, directory_source_id=source.id, external_id=key)
        for key in sorted(account_keys - existing)
    )
    return all(key is not None for key in keys)
