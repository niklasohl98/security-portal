"""SSO-Konten werden ausschließlich über (Tenant-ID, Objekt-ID) zugeordnet, nie über E-Mail."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.sso_provider import SsoError
from app.auth.sso_settings import SsoSettings
from app.db.models import AppUser, AuditLog, utcnow


def login_user(session: Session, cfg: SsoSettings, oid: str, is_admin: bool) -> int:
    """Legt eine externe Identität ohne lokales Passwort an oder aktualisiert ihren Login.

    Deaktivierte Konten werden niemals automatisch reaktiviert. Namenskollisionen und ein
    gleichzeitiges erstmaliges Anlegen werden abgelehnt; der Benutzer kann erneut anmelden.
    """
    user = session.scalars(
        select(AppUser).where(
            AppUser.oidc_tenant_id == cfg.tenant_id,
            AppUser.oidc_object_id == oid,
        )
    ).one_or_none()
    now = utcnow()
    if user is None:
        name = f"Entra-{oid}"
        user = AppUser(
            username=name,
            username_key=name.casefold(),
            password_hash="!",  # noqa: S106 - ungültiger Hash sperrt den lokalen Passwortzugang
            must_change_password=False,
            is_active=True,
            oidc_tenant_id=cfg.tenant_id,
            oidc_object_id=oid,
            is_admin=is_admin,
            created_at=now,
        )
        session.add(user)
    if not user.is_active:
        raise SsoError("disabled")
    user.is_admin = is_admin
    user.last_login_at = now
    try:
        session.flush()
    except IntegrityError:
        raise SsoError("identity") from None
    session.add(
        AuditLog(
            actor=f"user:{user.id}",
            action="auth.sso_login",
            at=now,
            detail=f"user_id={user.id}",
        )
    )
    return user.id
