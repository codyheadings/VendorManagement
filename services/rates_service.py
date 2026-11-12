"""Historical exchange rates from exchangeratesapi.io, cached on disk.
"""
import json
import os
from pathlib import Path
from urllib.parse import quote

import requests

import db

API_URL = "https://api.exchangeratesapi.io/v1/{date}"
SYMBOLS = ["USD", "GBP", "INR", "CNY", "CHF", "JPY", "CAD", "AUD"]
CACHE_DIR = db.BASE_DIR / "data" / "fixtures"
TIMEOUT_SECONDS = 10


class RatesUnavailable(Exception):
    """Any expected failure. The message is safe to print (key redacted)."""


def cache_path(date: str) -> Path:
    return CACHE_DIR / f"exchange_rates_{date}.json"


def get_api_key() -> str | None:
    """Environment variable first, then Streamlit secrets. None if neither is set."""
    key = os.environ.get("EXCHANGE_RATES_API_KEY")
    if key:
        return key
    try:
        import streamlit as st

        return st.secrets["EXCHANGE_RATES_API_KEY"]
    except Exception:
        return None


def _redact(text: str, key: str) -> str:
    for variant in {key, quote(key, safe="")}:
        text = text.replace(variant, "***")
    return text


def fetch_eur_rates(date: str, api_key: str) -> dict:
    """Make ONE API call. Returns {'date', 'base': 'EUR', 'rates': {...}} or raises RatesUnavailable."""
    try:
        resp = requests.get(
            API_URL.format(date=date),
            params={"access_key": api_key, "symbols": ",".join(SYMBOLS)},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        # str(e) contains the full URL, key included, so redact it. `from None`
        # also keeps the original exception out of any traceback.
        raise RatesUnavailable(f"request failed: {_redact(str(e), api_key)}") from None

    try:
        data = resp.json()
    except ValueError:
        raise RatesUnavailable(f"HTTP {resp.status_code}: response was not JSON") from None

    if not isinstance(data, dict) or not data.get("success"):
        err = (data.get("error") if isinstance(data, dict) else None) or {}
        detail = err.get("info") or err.get("type") or "unknown error"
        raise RatesUnavailable(_redact(f"API error {err.get('code', resp.status_code)}: {detail}", api_key))

    rates = data.get("rates")
    if data.get("base") != "EUR" or not isinstance(rates, dict):
        raise RatesUnavailable("unexpected response shape (expected EUR-based rates)")

    clean = {
        cur: float(val)
        for cur, val in rates.items()
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0
    }
    if "USD" not in clean:
        raise RatesUnavailable("response contained no usable USD rate")
    return {"date": data.get("date") or date, "base": "EUR", "rates": clean}


def to_usd_rates(payload: dict) -> dict[str, float]:
    """Convert EUR-based rates (units per 1 EUR) to {currency: USD per 1 unit}."""
    per_eur = payload["rates"]
    usd_per_eur = per_eur["USD"]
    out = {"EUR": usd_per_eur}
    for cur, units_per_eur in per_eur.items():
        out[cur] = 1.0 if cur == "USD" else usd_per_eur / units_per_eur
    return {cur: round(rate, 6) for cur, rate in out.items()}


def _read_cache(path: Path) -> dict | None:
    """Return the cached payload, or None if missing or unreadable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("base") == "EUR" and payload["rates"]["USD"] > 0:
            return payload
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_rates(date: str, allow_network: bool = False) -> tuple[dict[str, float] | None, str]:
    """Return (usd_rates, source_note); usd_rates is None when nothing is available.

    Order: cached file -> (only if allow_network) one live API call, then cached.
    """
    path = cache_path(date)

    cached = _read_cache(path)
    if cached:
        return to_usd_rates(cached), f"cached exchangeratesapi.io rates for {cached['date']} ({path.name})"

    if not allow_network:
        return None, f"no cached rates for {date} and network calls are off"

    key = get_api_key()
    if not key:
        return None, "EXCHANGE_RATES_API_KEY not found in environment or st.secrets"

    try:
        payload = fetch_eur_rates(date, key)
    except RatesUnavailable as e:
        return None, f"exchangeratesapi.io call failed: {e}"

    _write_cache(path, payload)
    return to_usd_rates(payload), f"live exchangeratesapi.io call for {payload['date']} (saved to {path.name})"