"""
Tests for the FRED client's parsing and error-handling logic.

As documented in src/ingestion/fred_client.py's module docstring: this
sandbox has no FRED API key, so these tests exercise the client against a
fixture built from FRED's *documented* JSON schema
(https://fred.stlouisfed.org/docs/api/fred/series_observations.html),
verified directly against the docs on 2026-09-08 — not against a captured
live response, unlike the SEC EDGAR fixtures. That distinction is the
point of this docstring, not an oversight.
"""
from __future__ import annotations

import pytest

from src.ingestion.fred_client import (
    FredAuthError,
    FredClient,
    _parse_observation,
    latest_observation,
)


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.last_call_params: dict | None = None

    def get(self, url, params=None, timeout=None):
        self.last_call_params = params
        return self._response


# A realistic payload shaped exactly per FRED's documented schema, with one
# genuinely missing observation (FRED encodes these as the literal string
# "." — a real quirk this parser must handle without fabricating a 0).
SAMPLE_PAYLOAD = {
    "realtime_start": "2026-09-08",
    "realtime_end": "2026-09-08",
    "observation_start": "1600-01-01",
    "observation_end": "9999-12-31",
    "units": "lin",
    "count": 3,
    "observations": [
        {"realtime_start": "2026-09-08", "realtime_end": "2026-09-08", "date": "2026-09-03", "value": "1.12"},
        {"realtime_start": "2026-09-08", "realtime_end": "2026-09-08", "date": "2026-09-04", "value": "."},
        {"realtime_start": "2026-09-08", "realtime_end": "2026-09-08", "date": "2026-09-05", "value": "1.15"},
    ],
}


def test_requires_api_key():
    with pytest.raises(FredAuthError):
        FredClient(api_key="")


def test_parses_observations_and_handles_missing_value_marker():
    session = _FakeSession(_FakeResponse(200, SAMPLE_PAYLOAD))
    client = FredClient(api_key="fake_test_key", session=session, requests_per_second=100.0)

    observations = client.get_observations("BAMLC0A4CBBB")

    assert len(observations) == 3
    assert observations[0].date == "2026-09-03"
    assert observations[0].value == pytest.approx(1.12)
    assert observations[1].value is None  # the "." marker, not 0.0
    assert session.last_call_params["api_key"] == "fake_test_key"
    assert session.last_call_params["series_id"] == "BAMLC0A4CBBB"


def test_400_response_raises_auth_error_not_generic_error():
    session = _FakeSession(_FakeResponse(400, text="Bad Request. Variable api_key is not set."))
    client = FredClient(api_key="wrong_key", session=session, requests_per_second=100.0)

    with pytest.raises(FredAuthError):
        client.get_observations("BAMLC0A4CBBB")


def test_latest_observation_skips_missing_values():
    observations = [_parse_observation(o) for o in SAMPLE_PAYLOAD["observations"]]
    latest = latest_observation(observations)
    assert latest is not None
    assert latest.date == "2026-09-05"  # not 09-04, which is missing ('.')
    assert latest.value == pytest.approx(1.15)
