"""Shared trust boundary for remote media URLs returned by catalog providers."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit, urlunsplit


CATALOG_MEDIA_HOSTS = {
    "myanimelist": ("myanimelist.net",),
    "anilist": ("anilist.co",),
}


def safe_catalog_media_url(value: Any, provider: str) -> str:
    """Accept only provider-owned HTTPS media hosts and never local IP literals."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048:
        return ""
    try:
        parsed = urlsplit(raw)
        hostname = str(parsed.hostname or "").rstrip(".").casefold()
        if parsed.scheme.casefold() != "https" or not hostname:
            return ""
        if parsed.username or parsed.password or parsed.port not in {None, 443}:
            return ""
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return ""
        allowed = CATALOG_MEDIA_HOSTS.get(str(provider or "").strip().casefold(), ())
        if not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in allowed):
            return ""
        return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))
    except (TypeError, ValueError):
        return ""
