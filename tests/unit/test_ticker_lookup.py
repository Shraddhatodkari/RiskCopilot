"""
Tests for ticker -> CIK resolution.

The sample payload below reproduces the real, verified-live shape and the
first three real entries of https://www.sec.gov/files/company_tickers.json
as fetched on 2026-09-08 (see docs/03_data_provenance.md) — NVDA, AAPL,
GOOGL with their real CIKs — used here as a fixture so these tests run
offline and deterministically.
"""
from __future__ import annotations

import json

import pytest
import requests

from src.ingestion import ticker_lookup as tl

REAL_SAMPLE_PAYLOAD = {
    "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "2": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_resolves_from_live_fetch_and_caches(tmp_path, monkeypatch):
    cache_path = tmp_path / "ticker_cache.json"

    monkeypatch.setattr(
        tl.requests, "get", lambda *a, **k: _FakeResponse(REAL_SAMPLE_PAYLOAD)
    )

    cik, name = tl.resolve_ticker("aapl", user_agent="Test test@example.com", cache_path=str(cache_path))
    assert cik == "0000320193"
    assert name == "Apple Inc."
    assert cache_path.exists()

    # Second call must use the cache, not hit the network again.
    def _boom(*a, **k):
        raise AssertionError("should not re-fetch: cache should have been used")

    monkeypatch.setattr(tl.requests, "get", _boom)
    cik2, name2 = tl.resolve_ticker(
        "NVDA", user_agent="Test test@example.com", cache_path=str(cache_path)
    )
    assert cik2 == "0001045810"
    assert name2 == "NVIDIA CORP"


def test_case_insensitive_lookup(tmp_path, monkeypatch):
    cache_path = tmp_path / "ticker_cache.json"
    monkeypatch.setattr(
        tl.requests, "get", lambda *a, **k: _FakeResponse(REAL_SAMPLE_PAYLOAD)
    )
    cik_lower, _ = tl.resolve_ticker("googl", user_agent="Test test@example.com", cache_path=str(cache_path))
    cik_upper, _ = tl.resolve_ticker("GOOGL", user_agent="Test test@example.com", cache_path=str(cache_path))
    assert cik_lower == cik_upper == "0001652044"


def test_unknown_ticker_raises(tmp_path, monkeypatch):
    cache_path = tmp_path / "ticker_cache.json"
    monkeypatch.setattr(
        tl.requests, "get", lambda *a, **k: _FakeResponse(REAL_SAMPLE_PAYLOAD)
    )
    with pytest.raises(tl.TickerLookupError):
        tl.resolve_ticker("NOPE_NOT_REAL", user_agent="Test test@example.com", cache_path=str(cache_path))


def test_falls_back_when_live_fetch_fails_and_no_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "ticker_cache.json"  # does not exist

    def _fail(*a, **k):
        raise requests.RequestException("network unreachable")

    monkeypatch.setattr(tl.requests, "get", _fail)

    cik, name = tl.resolve_ticker(
        "MSFT", user_agent="Test test@example.com", cache_path=str(cache_path)
    )
    assert cik == "0000789019"
    assert name == "Microsoft Corporation"


def test_stale_cache_is_refetched(tmp_path, monkeypatch):
    cache_path = tmp_path / "ticker_cache.json"
    cache_path.write_text(json.dumps({"OLD": ["0000000001", "Stale Co"]}))
    # Force the cache to look old.
    import os

    old_time = 0  # epoch — guaranteed older than any TTL
    os.utime(cache_path, (old_time, old_time))

    monkeypatch.setattr(
        tl.requests, "get", lambda *a, **k: _FakeResponse(REAL_SAMPLE_PAYLOAD)
    )
    cik, name = tl.resolve_ticker(
        "AAPL", user_agent="Test test@example.com", cache_path=str(cache_path), cache_ttl_seconds=3600
    )
    assert cik == "0000320193"
    assert name == "Apple Inc."
