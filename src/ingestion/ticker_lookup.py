"""
Ticker -> CIK resolution against SEC's own authoritative mapping file
(`https://www.sec.gov/files/company_tickers.json`), replacing Phase 1's
hard-coded two-ticker map.

Verified live on 2026-09-08 (see docs/03_data_provenance.md): the file is a
JSON object keyed by arbitrary string indices, each value shaped like
`{"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}` — NOT an
array, and the ticker field is not guaranteed unique-cased, so lookups are
case-insensitive.

Design choices:
- Cached to a local JSON file (`data/ticker_cache.json` by default) with a
  configurable TTL, because this file covers essentially every US-listed
  company (thousands of entries) and does not change intraday — re-fetching
  it on every CLI invocation would be wasteful and would count against the
  same rate-limit budget as the actual filing data being requested.
- Falls back to a small, hard-coded set of well-known tickers
  (`FALLBACK_TICKERS`, the same two validated in Phase 1) if the live fetch
  fails and no usable cache exists, so the tool degrades gracefully instead
  of becoming completely unusable when offline — but this fallback is
  logged loudly, never silent, so a user isn't confused about why an
  otherwise-valid ticker "isn't found."
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DEFAULT_CACHE_PATH = "data/ticker_cache.json"
DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 1 week

# Same companies validated end-to-end in Phase 1/2 — used only if a live
# fetch fails and no cache is available.
FALLBACK_TICKERS: dict[str, tuple[str, str]] = {
    "AAPL": ("0000320193", "Apple Inc."),
    "MSFT": ("0000789019", "Microsoft Corporation"),
}


class TickerLookupError(RuntimeError):
    pass


def _normalize_cik(cik_str: int | str) -> str:
    return str(int(cik_str)).zfill(10)


def _fetch_live(user_agent: str, timeout_seconds: float = 30.0) -> dict[str, tuple[str, str]]:
    response = requests.get(
        TICKERS_URL, headers={"User-Agent": user_agent}, timeout=timeout_seconds
    )
    response.raise_for_status()
    raw = response.json()
    mapping: dict[str, tuple[str, str]] = {}
    for entry in raw.values():
        ticker = str(entry["ticker"]).upper()
        mapping[ticker] = (_normalize_cik(entry["cik_str"]), entry["title"])
    return mapping


def _load_cache(cache_path: Path, ttl_seconds: float) -> dict[str, tuple[str, str]] | None:
    if not cache_path.exists():
        return None
    age_seconds = time.time() - cache_path.stat().st_mtime
    if age_seconds > ttl_seconds:
        return None
    try:
        raw = json.loads(cache_path.read_text())
        return {k: tuple(v) for k, v in raw.items()}
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("Ticker cache at %s is corrupt; ignoring.", cache_path)
        return None


def _save_cache(cache_path: Path, mapping: dict[str, tuple[str, str]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(mapping))


def resolve_ticker(
    ticker: str,
    user_agent: str,
    cache_path: str = DEFAULT_CACHE_PATH,
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
) -> tuple[str, str]:
    """Return (cik, entity_name) for a ticker symbol, or raise TickerLookupError."""
    ticker_upper = ticker.upper()
    path = Path(cache_path)

    mapping = _load_cache(path, cache_ttl_seconds)
    if mapping is None:
        try:
            mapping = _fetch_live(user_agent)
            _save_cache(path, mapping)
        except (requests.RequestException, ValueError, KeyError) as exc:
            logger.warning(
                "Live ticker lookup failed (%s); falling back to the small "
                "hard-coded validated set (AAPL, MSFT only).",
                exc,
            )
            mapping = FALLBACK_TICKERS

    if ticker_upper not in mapping:
        raise TickerLookupError(
            f"Ticker '{ticker}' not found in SEC's company_tickers.json "
            f"(or in the offline fallback set, if that's what was used)."
        )
    return mapping[ticker_upper]
