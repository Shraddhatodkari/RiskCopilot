"""
Thin, well-behaved HTTP client for SEC EDGAR's public XBRL "frames"/"facts"
APIs (data.sec.gov).

Why a hand-rolled client instead of a third-party "sec-edgar" package:
those wrappers are unmaintained or add heavy dependencies for what is really
about 40 lines of disciplined HTTP handling. Given the project's hard
constraint of running on a low-spec laptop with `requests` as essentially
the only network dependency, writing this directly keeps the dependency
footprint minimal and keeps rate-limiting/retry behavior fully auditable.

Confirmed against SEC's own published policy (docs/03_data_provenance.md
cites the sources): max 10 requests/second per IP, User-Agent header is
mandatory and must contain a contact email, and 403/429 responses should
back off rather than being retried immediately.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from src.config import Settings

logger = logging.getLogger(__name__)

USER_AGENT_HELP_URL = "https://www.sec.gov/os/webmaster-faq#developers"


class SecEdgarError(RuntimeError):
    """Base class for SEC EDGAR client errors."""


class SecEdgarNotFound(SecEdgarError):
    """The requested CIK/concept does not exist (HTTP 404)."""


class SecEdgarRateLimited(SecEdgarError):
    """SEC EDGAR returned 403/429 — caller is being throttled or blocked."""


@dataclass
class _RateLimiter:
    """A simple token-less rate limiter: sleeps just enough to keep the
    average request rate at or below `requests_per_second`.

    A token-bucket or leaky-bucket implementation would be overkill for a
    single-threaded research tool making, at most, a few dozen requests per
    ingestion run — this is intentionally the simplest thing that is
    correct.
    """

    requests_per_second: float
    _last_call_monotonic: float | None = None

    def wait(self) -> None:
        min_interval = 1.0 / self.requests_per_second
        now = time.monotonic()
        if self._last_call_monotonic is not None:
            elapsed = now - self._last_call_monotonic
            remaining = min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_call_monotonic = time.monotonic()


class SecEdgarClient:
    """Rate-limited, retrying client for the data.sec.gov XBRL API."""

    BASE_URL = "https://data.sec.gov"

    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self._settings = settings
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": settings.sec_edgar_user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self._rate_limiter = _RateLimiter(settings.sec_requests_per_second)

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.BASE_URL}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self._settings.max_retries + 1):
            self._rate_limiter.wait()
            try:
                response = self._session.get(
                    url, timeout=self._settings.request_timeout_seconds
                )
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning(
                    "Network error calling %s (attempt %d/%d): %s",
                    url,
                    attempt,
                    self._settings.max_retries,
                    exc,
                )
                time.sleep(min(2 ** attempt, 30))
                continue

            if response.status_code == 200:
                return response.json()
            if response.status_code == 404:
                raise SecEdgarNotFound(f"404 from {url}")
            if response.status_code in (403, 429):
                logger.warning(
                    "SEC EDGAR rate-limited/blocked us on %s (status %d, "
                    "attempt %d/%d). Backing off.",
                    url,
                    response.status_code,
                    attempt,
                    self._settings.max_retries,
                )
                time.sleep(min(60, 5 * attempt))
                last_exc = SecEdgarRateLimited(
                    f"HTTP {response.status_code} from {url}. Check that "
                    f"SEC_EDGAR_USER_AGENT is set correctly per {USER_AGENT_HELP_URL}."
                )
                continue
            # Other 4xx/5xx: retry with backoff, then give up.
            last_exc = SecEdgarError(f"HTTP {response.status_code} from {url}")
            time.sleep(min(2 ** attempt, 30))

        assert last_exc is not None
        if not isinstance(last_exc, SecEdgarError):
            # A raw requests.RequestException (e.g. repeated timeouts/connection
            # errors) survived every retry. Wrap it so callers can rely on the
            # single contract "failures from this client are SecEdgarError",
            # consistent with the 403/429 path above which already wraps in
            # SecEdgarRateLimited. See tests/unit/test_sec_edgar_client_retry.py
            # ::test_persistent_network_error_eventually_raises.
            raise SecEdgarError(
                f"Failed after {self._settings.max_retries} attempts calling {url}: {last_exc}"
            ) from last_exc
        raise last_exc

    def get_company_concept(self, cik: str, taxonomy: str, tag: str) -> dict[str, Any]:
        """Fetch a single XBRL concept (e.g. us-gaap/Assets) for one company.

        `cik` may be given with or without leading zeros / the "CIK" prefix;
        it is normalized to the required 10-digit zero-padded form.
        """
        cik_10 = self._normalize_cik(cik)
        return self._get(f"/api/xbrl/companyconcept/CIK{cik_10}/{taxonomy}/{tag}.json")

    def get_company_facts(self, cik: str) -> dict[str, Any]:
        """Fetch ALL XBRL facts SEC has for one company (large payload)."""
        cik_10 = self._normalize_cik(cik)
        return self._get(f"/api/xbrl/companyfacts/CIK{cik_10}.json")

    def get_submissions(self, cik: str) -> dict[str, Any]:
        """Fetch a company's filing history/metadata (form, accession
        number, filing date, primary document filename per filing) — the
        real, live equivalent of the paginated arrays each fixture's
        `_provenance` note describes. Same host (data.sec.gov) as every
        other endpoint here, so it reuses `_get`'s retry/rate-limit logic
        unchanged. Used by `src/ingestion/filing_document_client.py` to
        resolve which HTML document a given accession number's 10-K
        actually is (ADR-013)."""
        cik_10 = self._normalize_cik(cik)
        return self._get(f"/submissions/CIK{cik_10}.json")

    def get_document_html(self, cik: str, accession_number: str, filename: str) -> str:
        """Fetch one filing's actual primary document (real HTML), from
        www.sec.gov/Archives — a DIFFERENT host than every other method on
        this class (data.sec.gov), and returning raw text rather than
        parsed JSON, hence its own small request loop rather than reusing
        `_get` (which is hardcoded to `self.BASE_URL` and always calls
        `.json()`). Same rate limiter, session, and User-Agent as every
        other request this client makes."""
        cik_unpadded = str(int(self._normalize_cik(cik)))
        accn_no_dashes = accession_number.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{accn_no_dashes}/{filename}"

        last_exc: Exception | None = None
        for attempt in range(1, self._settings.max_retries + 1):
            self._rate_limiter.wait()
            try:
                response = self._session.get(url, timeout=self._settings.request_timeout_seconds)
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("Network error fetching document %s (attempt %d/%d): %s",
                                url, attempt, self._settings.max_retries, exc)
                time.sleep(min(2 ** attempt, 30))
                continue

            if response.status_code == 200:
                return response.text
            if response.status_code == 404:
                raise SecEdgarNotFound(f"404 from {url}")
            if response.status_code in (403, 429):
                logger.warning("SEC EDGAR rate-limited/blocked us fetching %s (status %d)",
                                url, response.status_code)
                time.sleep(min(60, 5 * attempt))
                last_exc = SecEdgarRateLimited(f"HTTP {response.status_code} from {url}")
                continue
            last_exc = SecEdgarError(f"HTTP {response.status_code} from {url}")
            time.sleep(min(2 ** attempt, 30))

        assert last_exc is not None
        if not isinstance(last_exc, SecEdgarError):
            raise SecEdgarError(
                f"Failed after {self._settings.max_retries} attempts fetching {url}: {last_exc}"
            ) from last_exc
        raise last_exc

    @staticmethod
    def _normalize_cik(cik: str) -> str:
        digits = "".join(ch for ch in str(cik) if ch.isdigit())
        if not digits:
            raise ValueError(f"CIK must contain digits, got: {cik!r}")
        return digits.zfill(10)
