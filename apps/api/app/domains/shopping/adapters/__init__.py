"""Resolve the backend site pack for an active session."""
from __future__ import annotations

from app.domains.shopping.adapters.base import SiteAdapter
from app.domains.shopping.adapters.ebay import adapter as ebay_adapter
from app.domains.shopping.adapters.jd import adapter as jd_adapter

_ADAPTERS: dict[str, SiteAdapter] = {
    jd_adapter.id: jd_adapter,
    ebay_adapter.id: ebay_adapter,
}


def get_adapter(site: str) -> SiteAdapter:
    site_id = str(site or "").strip().lower()
    try:
        return _ADAPTERS[site_id]
    except KeyError as exc:
        raise ValueError(f"adapter_missing:{site_id or '(empty)'}") from exc


__all__ = ["SiteAdapter", "get_adapter"]
