"""Produktionsstart mit JSON-Logging ab der ersten Uvicorn-Zeile und festen Proxy-Grenzen."""

from __future__ import annotations

import ipaddress
import os

import uvicorn

from app.config import ConfigError
from app.logging_config import configure_logging


def trusted_proxy_ips(value: str) -> str:
    """Akzeptiert ausschließlich konkrete IPs/CIDRs, niemals eine universelle Vertrauensliste."""
    values = value.split(",")
    try:
        for item in values:
            network = ipaddress.ip_network(item.strip(), strict=False)
            if network.prefixlen == 0:
                raise ValueError
    except ValueError:
        raise ConfigError(
            "KW_TRUSTED_PROXY_IPS benötigt konkrete IPs/CIDRs ohne Wildcard"
        ) from None
    return ",".join(item.strip() for item in values)


def main() -> None:
    """Startet genau einen Prozess; der Scheduler und seine Sperren sind prozesslokal."""
    configure_logging(os.environ.get("KW_LOG_LEVEL", "INFO"))
    proxies = trusted_proxy_ips(os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - Container, Hostbindung in Compose
        port=8000,
        workers=1,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=proxies,
    )


if __name__ == "__main__":
    main()
