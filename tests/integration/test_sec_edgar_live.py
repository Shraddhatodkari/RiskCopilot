"""
Live integration test against the real data.sec.gov API.

Honesty note (required reading before trusting a green run of the rest of
this suite): the cloud sandbox this project was originally developed in
enforces an organization-level network egress allowlist that does NOT
include data.sec.gov, so this specific test cannot pass inside that
sandbox — every real SEC number used elsewhere in this test suite
(tests/fixtures/*.json) was instead fetched live via a web-fetch tool
during development and frozen into fixtures, with the exact retrieval date
and method recorded in each fixture's "_provenance" field and in
docs/03_data_provenance.md.

On a normal machine (including the Windows laptop this project targets),
data.sec.gov is reachable directly and this test exercises the real,
unmodified `SecEdgarClient` end-to-end: real HTTP, real rate limiting, real
JSON parsing. Run it explicitly with:

    pytest -m integration -v

It is excluded from the default `pytest` run (see pytest.ini) so that CI /
offline development is never blocked by network availability, and it skips
itself (rather than failing) if the network is unreachable, so a developer
on a restricted network gets an honest "skipped, here's why" instead of a
red herring failure.
"""
from __future__ import annotations

import pytest
import requests

from src.config import load_settings
from src.ingestion.sec_edgar_client import SecEdgarClient
from src.ingestion.xbrl_facts import build_financial_snapshot

pytestmark = pytest.mark.integration


def _sec_edgar_reachable() -> bool:
    try:
        requests.get(
            "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Assets.json",
            headers={"User-Agent": "RiskCopilotConnectivityCheck test@example.com"},
            timeout=5,
        )
        return True
    except requests.RequestException:
        return False


@pytest.fixture(scope="module")
def live_client() -> SecEdgarClient:
    try:
        settings = load_settings()
    except Exception:
        pytest.skip(
            "SEC_EDGAR_USER_AGENT is not configured — copy .env.example to "
            ".env and set a real contact email before running integration tests."
        )
    if not _sec_edgar_reachable():
        pytest.skip(
            "data.sec.gov is not reachable from this network (this is expected "
            "inside the cloud development sandbox's egress allowlist — see the "
            "module docstring). Run this test from a network with normal "
            "internet access, e.g. the target Windows laptop."
        )
    return SecEdgarClient(settings)


def test_live_apple_total_assets_is_a_real_recent_number(live_client):
    """Loose sanity check, not an exact-value check: Apple's real total
    assets fluctuate every fiscal year, so this asserts the fact is
    internally consistent and plausible rather than pinning an exact
    figure that would go stale and start failing for a reason unrelated to
    a real bug."""
    snapshot = build_financial_snapshot(
        live_client, cik="0000320193", entity_name="Apple Inc.", fiscal_year=2025
    )
    assert snapshot.total_assets is not None
    # Apple's total assets have been in the low-to-mid hundreds of billions
    # for the last several fiscal years; a plausibility band catches gross
    # parsing errors (e.g. picking up a per-share or percentage value)
    # without pinning an exact figure that will legitimately change.
    assert 200_000_000_000 < snapshot.total_assets.value < 800_000_000_000
    assert snapshot.total_assets.form == "10-K"
    assert snapshot.total_assets.accession_number
