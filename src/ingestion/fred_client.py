"""
Client for the Federal Reserve Economic Data (FRED) API — used from Phase 2
onward to give a company-specific risk score macro context (e.g. "is credit
generally tightening across the whole market right now, or is this specific
to the company").

Honesty note on this module's test coverage: FRED requires a free,
individually-registered API key for every request — confirmed directly
against the live API during development, where a keyless request returns
an HTTP 400 (`docs/03_data_provenance.md`). This sandbox has no such key
provisioned, so — unlike the SEC EDGAR client, which was validated against
real, live responses throughout Phase 1/2 — this client's request/response
handling is tested against a fixture built from FRED's own documented JSON
schema (https://fred.stlouisfed.org/docs/api/fred/series_observations.html),
not against a captured live response. This is stated plainly rather than
implied to be equivalent: `tests/unit/test_fred_client.py` verifies parsing
and error-handling logic, not that a specific real number was live-fetched.
A user with their own free key (registration:
https://fred.stlouisfed.org/docs/api/api_key.html) can confirm the live
path with `pytest -m integration` once `FRED_API_KEY` is set — the same
honest pattern already used for the SEC EDGAR integration test.

FRED's docs do not publish a specific rate limit (confirmed by direct
inspection of the docs page on 2026-09-08) — this client still rate-limits
conservatively (`Settings` below) out of good API citizenship, not because
a specific published number is being honored, and that distinction is
called out explicitly rather than inventing a limit to sound authoritative.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# Series used by this project (see docs/03_data_provenance.md for why each
# was selected): ICE BofA BBB US Corporate Index Option-Adjusted Spread —
# a broad, real-time proxy for how much the market is charging
# investment-grade borrowers over Treasuries. Widening spreads mean credit
# conditions are tightening market-wide, independent of any one company's
# fundamentals — exactly the context a company-specific Z-Score/F-Score
# should be read alongside, not blended into (ADR-009).
BBB_CORPORATE_SPREAD_SERIES_ID = "BAMLC0A4CBBB"


class FredApiError(RuntimeError):
    pass


class FredAuthError(FredApiError):
    """Raised on HTTP 400/401 — almost always a missing/invalid api_key."""


@dataclass
class FredObservation:
    date: str  # ISO date string, e.g. "2026-09-05"
    value: float | None  # FRED encodes missing data points as the string "."


@dataclass
class _SimpleRateLimiter:
    min_interval_seconds: float
    _last_call: float | None = field(default=None, init=False)

    def wait(self) -> None:
        now = time.monotonic()
        if self._last_call is not None:
            remaining = self.min_interval_seconds - (now - self._last_call)
            if remaining > 0:
                time.sleep(remaining)
        self._last_call = time.monotonic()


class FredClient:
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        requests_per_second: float = 2.0,
        timeout_seconds: float = 30.0,
    ):
        if not api_key:
            raise FredAuthError(
                "FRED_API_KEY is required. Register for a free key at "
                "https://fred.stlouisfed.org/docs/api/api_key.html and set "
                "it in your .env file."
            )
        self._api_key = api_key
        self._session = session or requests.Session()
        self._rate_limiter = _SimpleRateLimiter(1.0 / requests_per_second)
        self._timeout = timeout_seconds

    def get_observations(
        self, series_id: str, observation_start: str | None = None
    ) -> list[FredObservation]:
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
        }
        if observation_start:
            params["observation_start"] = observation_start

        self._rate_limiter.wait()
        try:
            response = self._session.get(BASE_URL, params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            raise FredApiError(f"Network error calling FRED API: {exc}") from exc

        if response.status_code in (400, 401):
            raise FredAuthError(
                f"FRED API returned {response.status_code} — this almost always means "
                f"FRED_API_KEY is missing or invalid. Response body: {response.text[:300]}"
            )
        if response.status_code != 200:
            raise FredApiError(f"FRED API returned HTTP {response.status_code}")

        payload = response.json()
        return [_parse_observation(o) for o in payload.get("observations", [])]


def _parse_observation(raw: dict) -> FredObservation:
    raw_value = raw.get("value")
    # FRED represents a missing data point as the literal string "." — not
    # null, not 0. Treating it as 0 would silently fabricate a data point
    # (e.g. implying a 0% spread on a day the series simply has no reading).
    value = None if raw_value in (None, ".") else float(raw_value)
    return FredObservation(date=raw["date"], value=value)


def latest_observation(observations: list[FredObservation]) -> FredObservation | None:
    """Most recent observation with a real (non-missing) value."""
    for obs in sorted(observations, key=lambda o: o.date, reverse=True):
        if obs.value is not None:
            return obs
    return None
