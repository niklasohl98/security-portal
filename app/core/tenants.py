"""Abgleich der Mandanten aus ``tenants.yaml`` in die Datenbank."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import TenantConfig
from app.db.models import Tenant

log = logging.getLogger(__name__)


def _apply(row: Tenant, cfg: TenantConfig) -> None:
    """Überträgt die YAML-Werte auf die Zeile (nur Namen von Secrets, nie deren Inhalt)."""
    row.name = cfg.name
    row.enabled = cfg.enabled
    row.entra_tenant_id = cfg.entra.tenant_id if cfg.entra else None
    row.entra_client_id = cfg.entra.client_id if cfg.entra else None
    row.entra_certificate_secret = cfg.entra.certificate_secret if cfg.entra else None
    row.entra_certificate_thumbprint = cfg.entra.certificate_thumbprint if cfg.entra else None
    row.ad_domain = cfg.ad.domain if cfg.ad else None
    row.ad_dcs = list(cfg.ad.dcs) if cfg.ad else []
    row.ad_bind_user = cfg.ad.bind_user if cfg.ad else None
    row.ad_bind_secret = cfg.ad.bind_secret if cfg.ad else None
    row.ad_ca_file = cfg.ad.ca_file if cfg.ad else None
    row.ad_weak_keys_until = cfg.ad.accept_weak_dc_keys_until if cfg.ad else None
    row.inactive_days = cfg.thresholds.inactive_days
    row.long_inactive_days = cfg.thresholds.long_inactive_days
    row.new_account_grace_days = cfg.thresholds.new_account_grace_days
    row.exception_days = cfg.thresholds.exception_days
    row.exception_warn_days = cfg.thresholds.exception_warn_days
    row.account_patterns = {k: list(v) for k, v in cfg.account_patterns.items()}
    row.report_recipients = list(cfg.report.recipients)


def sync_tenants(session: Session, configs: list[TenantConfig]) -> None:
    """Legt Mandanten an und aktualisiert sie anhand der YAML (Kürzel ist der Schlüssel).

    Die YAML ist die Quelle der Wahrheit: Änderungen an Mandantenfeldern in der DB werden
    beim Start überschrieben. Mandanten, die in der YAML fehlen, werden deaktiviert, nicht
    gelöscht, damit ihre Daten und Ausnahmen erhalten bleiben.

    Args:
        session: Offene Sitzung; der Aufrufer committet.
        configs: Validierte Mandanten aus ``load_tenants``.
    """
    existing = {t.slug: t for t in session.scalars(select(Tenant))}
    for cfg in configs:
        row = existing.pop(cfg.slug, None)
        if row is None:
            row = Tenant(slug=cfg.slug)
            session.add(row)
            log.info("Mandant angelegt: %s", cfg.slug)
        _apply(row, cfg)
    for slug, row in existing.items():
        if row.enabled:
            row.enabled = False
            log.warning("Mandant nicht mehr in der YAML, deaktiviert: %s", slug)
