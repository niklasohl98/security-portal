"""SQLAlchemy-Modelle.

Mandantentrennung: Jede Tabelle mit Kundendaten trägt ``tenant_id``; jede Abfrage filtert danach.
Ausnahmen ohne ``tenant_id``: ``tenant`` selbst, ``sku_name`` (globale Zuordnung
Teilenummer -> Anzeigename, enthält keine Kundendaten) sowie ``app_user`` und ``app_session``: die
Benutzer des Tools gehören der Softchem-IT und keinem Mandanten (ADR 0007).
Alle Zeitwerte sind UTC und timezone-aware.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Aktuelle Zeit in UTC (timezone-aware)."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """DateTime, das nur timezone-aware Werte annimmt und immer UTC-aware zurückgibt.

    SQLite speichert keine Zeitzone; ohne diesen Typ kämen naive Werte zurück.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime ist nicht erlaubt; Zeiten immer mit Zeitzone")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Source(enum.StrEnum):
    """Quelle eines Abrufs."""

    ENTRA = "entra"
    AD = "ad"


class RunStatus(enum.StrEnum):
    """Status eines Abrufs."""

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


def _enum(e: type[enum.StrEnum], name: str) -> SAEnum:
    # Als VARCHAR mit Prüfregel speichern (portabel für SQLite und Postgres).
    return SAEnum(
        e,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda x: [m.value for m in x],
        length=16,
    )


class Base(DeclarativeBase):
    """Basisklasse aller Modelle."""

    # Benannte Constraints, damit spätere Migrationen (SQLite-Batch-Modus) sie ansprechen können.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime}


class Tenant(Base):
    """Mandant (Kunde). Enthält nur *Namen* von Secrets, nie deren Werte."""

    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    entra_tenant_id: Mapped[str | None] = mapped_column(String(36))
    entra_client_id: Mapped[str | None] = mapped_column(String(36))
    entra_certificate_secret: Mapped[str | None] = mapped_column(String(128))
    entra_certificate_thumbprint: Mapped[str | None] = mapped_column(String(40))
    ad_domain: Mapped[str | None] = mapped_column(String(255))
    ad_dcs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    ad_bind_user: Mapped[str | None] = mapped_column(String(255))
    ad_bind_secret: Mapped[str | None] = mapped_column(String(128))
    ad_ca_file: Mapped[str | None] = mapped_column(String(128))
    ad_weak_keys_until: Mapped[date | None]
    inactive_days: Mapped[int] = mapped_column(Integer, default=30)
    long_inactive_days: Mapped[int] = mapped_column(Integer, default=90)
    new_account_grace_days: Mapped[int] = mapped_column(Integer, default=14)
    exception_days: Mapped[int] = mapped_column(Integer, default=365, server_default="365")
    exception_warn_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    account_patterns: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Empfänger des Wochenberichts (aus der YAML; Adressen nie in Log oder Audit-Log).
    report_recipients: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class DirectorySource(Base):
    """Dauerhafter Verzeichnis-Namensraum; keine Ableitung aus wechselnden Anmeldenamen."""

    __tablename__ = "directory_source"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "source", "external_id", name="uq_directory_source_identity"),
        CheckConstraint(
            "length(external_id) > 0 AND external_id = lower(trim(external_id))",
            name="canonical_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    source: Mapped[Source] = mapped_column(_enum(Source, "source"))
    external_id: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SourceAccount(Base):
    """Quellkonto unabhängig von einzelnen Abrufständen und der aktuellen Zusammenführung."""

    __tablename__ = "source_account"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "directory_source_id", "external_id", name="uq_source_account_identity"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "directory_source_id"],
            ["directory_source.tenant_id", "directory_source.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "length(external_id) > 0 AND external_id = lower(trim(external_id))",
            name="canonical_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    directory_source_id: Mapped[int] = mapped_column(Integer)
    external_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SyncRun(Base):
    """Ein Abruf je Mandant und Quelle."""

    __tablename__ = "sync_run"
    __table_args__ = (Index("ix_sync_run_tenant_started", "tenant_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    source: Mapped[Source] = mapped_column(_enum(Source, "source"))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "run_status"))
    account_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)  # nur Fehlerart, keine Inhalte
    dcs_checked: Mapped[int] = mapped_column(Integer, default=0)
    dcs_defective: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # Feste Warncodes (z. B. "sign_in_activity_unavailable"), von der Oberfläche übersetzt.
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list)


class EntraAccount(Base):
    """Rohdaten eines Entra-Kontos je Lauf."""

    __tablename__ = "entra_account"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "object_id"),
        Index("ix_entra_account_tenant_run", "tenant_id", "sync_run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("sync_run.id", ondelete="CASCADE"))
    object_id: Mapped[str] = mapped_column(String(36))
    user_principal_name: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(256))
    mail: Mapped[str | None] = mapped_column(String(320))
    user_type: Mapped[str | None] = mapped_column(String(32))
    account_enabled: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime | None]
    on_prem_sync_enabled: Mapped[bool | None] = mapped_column(Boolean)
    on_prem_immutable_id: Mapped[str | None] = mapped_column(String(64))
    on_prem_sam_account_name: Mapped[str | None] = mapped_column(String(256))
    last_successful_sign_in: Mapped[datetime | None]
    last_non_interactive_sign_in: Mapped[datetime | None]
    last_sign_in: Mapped[datetime | None]  # zählt auch Fehlversuche, nur Anzeige
    licenses: Mapped[list[Any]] = mapped_column(JSON, default=list)


class TenantSku(Base):
    """Lizenzbestand (SKU) eines Mandanten je Entra-Lauf (``GET /subscribedSkus``)."""

    __tablename__ = "tenant_sku"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "sku_id"),
        Index("ix_tenant_sku_tenant_run", "tenant_id", "sync_run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("sync_run.id", ondelete="CASCADE"))
    sku_id: Mapped[str] = mapped_column(String(36))
    part_number: Mapped[str] = mapped_column(String(100))
    applies_to: Mapped[str | None] = mapped_column(String(32))
    capability_status: Mapped[str | None] = mapped_column(String(32))
    enabled_units: Mapped[int] = mapped_column(Integer, default=0)
    consumed_units: Mapped[int] = mapped_column(Integer, default=0)


class AdAccount(Base):
    """Rohdaten eines AD-Kontos je Lauf."""

    __tablename__ = "ad_account"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "object_guid"),
        Index("ix_ad_account_tenant_run", "tenant_id", "sync_run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("sync_run.id", ondelete="CASCADE"))
    object_guid: Mapped[str] = mapped_column(String(36))
    sam_account_name: Mapped[str | None] = mapped_column(String(256))
    user_principal_name: Mapped[str | None] = mapped_column(String(320))
    mail: Mapped[str | None] = mapped_column(String(320))
    user_account_control: Mapped[int | None] = mapped_column(BigInteger)
    when_created: Mapped[datetime | None]
    pwd_last_set: Mapped[datetime | None]
    last_logon: Mapped[datetime | None]  # Maximum über alle DCs
    last_logon_per_dc: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_logon_timestamp: Mapped[datetime | None]
    account_expires: Mapped[datetime | None]
    description: Mapped[str | None] = mapped_column(Text)
    distinguished_name: Mapped[str | None] = mapped_column(Text)
    ou: Mapped[str | None] = mapped_column(Text)
    member_of: Mapped[list[Any]] = mapped_column(JSON, default=list)
    master_data_incomplete: Mapped[bool] = mapped_column(Boolean, default=False)


class Identity(Base):
    """Zusammengeführte Person je Mandant (Ergebnis von Merge und Regeln, Phase 4).

    ``key`` ist über Läufe hinweg stabil (z. B. objectGUID bzw. Entra-Objekt-ID), damit
    Ausnahmen erhalten bleiben. Die Verweise auf die Rohdaten zeigen auf den jüngsten Lauf.
    """

    __tablename__ = "identity"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    entra_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("entra_account.id", ondelete="SET NULL")
    )
    ad_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("ad_account.id", ondelete="SET NULL")
    )
    display_name: Mapped[str | None] = mapped_column(String(256))
    last_login_at: Mapped[datetime | None]
    account_type: Mapped[str | None] = mapped_column(String(32))
    account_type_override: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(32))
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    recommendation: Mapped[str | None] = mapped_column(Text)
    # Verschwunden: im jüngsten Lauf beider Quellen ohne Konto. Die Zeile bleibt, bis jemand in der
    # Prüfliste entscheidet (``review_decision`` = "keep" oder die Zeile wird gelöscht).
    missing_since: Mapped[datetime | None]
    review_decision: Mapped[str | None] = mapped_column(String(16))
    reviewed_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    exceptions: Mapped[list[AccountException]] = relationship(
        back_populates="identity", cascade="all, delete-orphan"
    )


class AccountException(Base):
    """Ausnahme von der Inaktivitätsprüfung (z. B. Break-Glass). Braucht Grund und Ablauf."""

    __tablename__ = "exception"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    identity_id: Mapped[int] = mapped_column(ForeignKey("identity.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    valid_until: Mapped[datetime]

    identity: Mapped[Identity] = relationship(back_populates="exceptions")


class SkuName(Base):
    """Zuordnung SKU-Teilenummer -> Anzeigename (global, gepflegte Tabelle)."""

    __tablename__ = "sku_name"

    part_number: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    is_free: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    """Wer hat wann was geändert oder exportiert. ``detail`` ohne Kontoinhalte."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_at", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenant.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(default=utcnow)
    actor: Mapped[str] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)


class AppUser(Base):
    """Lokaler Benutzer des Tools (Softchem-IT), nicht mandantenbezogen (ADR 0007).

    ``username_key`` ist der kleingeschriebene Name für Eindeutigkeit und Suche. Das Passwort steht
    nur als Argon2id-Hash in der Datenbank. ``must_change_password`` erzwingt vor jeder weiteren
    Aktion einen Passwortwechsel (Initialkonto, Rücksetzen).
    """

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("oidc_tenant_id", "oidc_object_id", name="uq_app_user_oidc_identity"),
        CheckConstraint(
            "(oidc_tenant_id IS NULL) = (oidc_object_id IS NULL)", name="oidc_identity_pair"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    username_key: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Admins verwalten Benutzer (anlegen, deaktivieren, zurücksetzen); sonst gleiche Rechte.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]
    password_changed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    oidc_tenant_id: Mapped[str | None] = mapped_column(String(36))
    oidc_object_id: Mapped[str | None] = mapped_column(String(36))


class AppSession(Base):
    """Serverseitige Sitzung. Das Cookie enthält nur das Token; hier steht dessen SHA-256-Hash."""

    __tablename__ = "app_session"
    __table_args__ = (Index("ix_app_session_user", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    is_oidc: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
