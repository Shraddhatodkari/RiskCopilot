"""Shared pytest fixtures.

`FixtureBackedClient` implements the same public interface as
`SecEdgarClient` (duck-typed) but is backed by a static JSON file captured
from real, live SEC EDGAR responses instead of the network. This lets unit
tests exercise the real parsing/fallback/data-quality logic in
`src/ingestion/xbrl_facts.py` deterministically and offline, while still
testing against genuine SEC data rather than invented numbers — see
tests/fixtures/*.json for the provenance note on each fixture and
docs/03_data_provenance.md for why this sandbox cannot make live outbound
calls to data.sec.gov during automated test runs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingestion.sec_edgar_client import SecEdgarNotFound

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FixtureBackedClient:
    def __init__(self, fixture_name: str):
        with open(FIXTURES_DIR / fixture_name) as f:
            self._data = json.load(f)

    def get_company_concept(self, cik: str, taxonomy: str, tag: str) -> dict:
        if tag not in self._data:
            raise SecEdgarNotFound(f"fixture has no tag {tag}")
        return self._data[tag]


@pytest.fixture
def aapl_client() -> FixtureBackedClient:
    return FixtureBackedClient("aapl_fy2025_companyconcept.json")


class TagHidingClient(FixtureBackedClient):
    """A real fixture with specific tags deliberately removed, so a tag
    lookup raises SecEdgarNotFound exactly as a live 404 would. Used ONLY to
    exercise the missing-data code paths (refuse to fabricate, report the
    gap) now that no real company fixture happens to lack an Altman input.
    Every value that remains is still genuine SEC data."""

    def __init__(self, fixture_name: str, hidden_tags: tuple[str, ...]):
        super().__init__(fixture_name)
        for tag in hidden_tags:
            self._data.pop(tag, None)


APPLE_EQUITY_TAGS = ("RetainedEarningsAccumulatedDeficit", "StockholdersEquity")


@pytest.fixture
def aapl_client_missing_equity_tags() -> FixtureBackedClient:
    """Real Apple data with RetainedEarningsAccumulatedDeficit and
    StockholdersEquity deliberately hidden — see TagHidingClient."""
    return TagHidingClient("aapl_fy2025_companyconcept.json", APPLE_EQUITY_TAGS)


@pytest.fixture
def msft_client() -> FixtureBackedClient:
    return FixtureBackedClient("msft_fy2025_companyconcept.json")


@pytest.fixture
def nvda_client() -> FixtureBackedClient:
    """NVIDIA Corporation — the third, independently real company this
    project uses (ADR-013, Section 18) to prove its pipeline is not
    limited to Apple/Microsoft. See tests/fixtures/
    nvda_fy2025_companyconcept.json's own `_provenance` field."""
    return FixtureBackedClient("nvda_fy2025_companyconcept.json")
