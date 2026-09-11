"""
Tests for SecEdgarClient's retry/backoff/error-handling behavior — the gap
explicitly called out as untested in docs/05_testing_strategy.md's Phase 1
write-up, closed here in Phase 5 using a fake `requests.Session` (no real
network, no real sleeping) so this exercises the exact retry logic in
`src/ingestion/sec_edgar_client.py._get` deterministically and fast.
"""
from __future__ import annotations

import pytest
import requests

from src.config import Settings
from src.ingestion.sec_edgar_client import (
    SecEdgarClient,
    SecEdgarError,
    SecEdgarNotFound,
    SecEdgarRateLimited,
)


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class _ScriptedSession:
    """Returns/raises a scripted sequence of results, one per call to .get()."""

    def __init__(self, script: list):
        self._script = list(script)
        self.call_count = 0
        self.headers: dict = {}  # SecEdgarClient.__init__ sets the User-Agent header here

    def get(self, url, timeout=None):
        self.call_count += 1
        if not self._script:
            raise AssertionError("no more scripted responses — client retried too many times")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fast_settings():
    # requests_per_second very high and max_retries small so tests run
    # instantly and deterministically regardless of the real rate limiter
    # / backoff sleep durations.
    return Settings(
        sec_edgar_user_agent="Test test@example.com",
        fred_api_key=None,
        anthropic_api_key=None,
        sec_requests_per_second=1000.0,
        max_retries=3,
        request_timeout_seconds=5.0,
    )


def test_succeeds_immediately_on_200(fast_settings, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _ScriptedSession([_FakeResponse(200, {"ok": True})])
    client = SecEdgarClient(fast_settings, session=session)

    result = client._get("/some/path.json")

    assert result == {"ok": True}
    assert session.call_count == 1


def test_retries_after_a_network_error_then_succeeds(fast_settings, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _ScriptedSession(
        [requests.exceptions.ConnectionError("boom"), _FakeResponse(200, {"ok": True})]
    )
    client = SecEdgarClient(fast_settings, session=session)

    result = client._get("/some/path.json")

    assert result == {"ok": True}
    assert session.call_count == 2


def test_retries_after_429_then_succeeds(fast_settings, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _ScriptedSession([_FakeResponse(429), _FakeResponse(200, {"ok": True})])
    client = SecEdgarClient(fast_settings, session=session)

    result = client._get("/some/path.json")

    assert result == {"ok": True}
    assert session.call_count == 2


def test_gives_up_after_max_retries_and_raises_rate_limited(fast_settings, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _ScriptedSession([_FakeResponse(403), _FakeResponse(403), _FakeResponse(403)])
    client = SecEdgarClient(fast_settings, session=session)

    with pytest.raises(SecEdgarRateLimited):
        client._get("/some/path.json")
    assert session.call_count == 3  # exactly max_retries, no more


def test_404_raises_immediately_without_retrying(fast_settings, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _ScriptedSession([_FakeResponse(404)])
    client = SecEdgarClient(fast_settings, session=session)

    with pytest.raises(SecEdgarNotFound):
        client._get("/some/path.json")
    assert session.call_count == 1  # 404 is not retried — it's a genuine "doesn't exist"


def test_persistent_network_error_eventually_raises(fast_settings, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _ScriptedSession(
        [requests.exceptions.Timeout("t1"), requests.exceptions.Timeout("t2"), requests.exceptions.Timeout("t3")]
    )
    client = SecEdgarClient(fast_settings, session=session)

    with pytest.raises(SecEdgarError):
        client._get("/some/path.json")
    assert session.call_count == 3
