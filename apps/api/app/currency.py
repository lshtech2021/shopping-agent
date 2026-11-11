"""Currency code normalization shared by session settings and requirements."""
from __future__ import annotations

SUPPORTED_CURRENCIES = {
    "CNY",
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CAD",
    "AUD",
    "HKD",
    "SGD",
    "NZD",
}

_ALIASES = {
    "RMB": "CNY",
    "人民币": "CNY",
    "US$": "USD",
    "$": "USD",
    "美元": "USD",
    "€": "EUR",
    "EURO": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "円": "JPY",
    "日元": "JPY",
}


def normalize_currency(value: str | None) -> str | None:
    code = str(value or "").strip().upper()
    if not code:
        return None
    return _ALIASES.get(code, code)


def is_supported_currency(value: str | None) -> bool:
    return normalize_currency(value) in SUPPORTED_CURRENCIES
