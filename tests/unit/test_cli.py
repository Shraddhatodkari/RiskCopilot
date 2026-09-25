"""
Tests for src/cli.py — a real, previously-uncovered gap (0% coverage) found
during the Phase 7 final audit's honest coverage review (see
docs/08_final_audit.md). These use the same fixture-backed-client pattern
as tests/conftest.py so the CLI's actual argument parsing, error handling,
and output/persistence logic is exercised without any real network call —
by monkeypatching `src.cli.SecEdgarClient` itself, exactly the seam the CLI
already exposes for this (it constructs one client instance and passes it
through).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import cli
from src.ingestion.sec_edgar_client import SecEdgarNotFound
from src.ingestion.ticker_lookup import TickerLookupError

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class _FixtureBackedClient:
    def __init__(self, *_args, **_kwargs):
        with open(FIXTURES_DIR / "msft_fy2025_companyconcept.json") as f:
            self._data = json.load(f)

    def get_company_concept(self, cik: str, taxonomy: str, tag: str) -> dict:
        if tag not in self._data:
            raise SecEdgarNotFound(f"fixture has no tag {tag}")
        return self._data[tag]


@pytest.fixture(autouse=True)
def _valid_env(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Test test@example.com")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_missing_user_agent_returns_config_error_exit_code(monkeypatch, capsys):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    exit_code = cli.main(["--cik", "0000789019", "--entity-name", "Microsoft Corporation", "--fiscal-year", "2025"])
    assert exit_code == 2
    assert "CONFIGURATION ERROR" in capsys.readouterr().err


def test_cik_missing_entity_name_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--cik", "0000789019", "--fiscal-year", "2025"])
    assert exc_info.value.code == 2  # argparse's usage-error exit code


def test_altman_only_run_prints_score_and_sources(monkeypatch, capsys):
    monkeypatch.setattr(cli, "SecEdgarClient", _FixtureBackedClient)

    exit_code = cli.main(
        ["--cik", "0000789019", "--entity-name", "Microsoft Corporation", "--fiscal-year", "2025"]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Altman Z'-Score:" in out
    assert "GREY" in out or "SAFE" in out or "DISTRESS" in out
    assert "Source facts" in out
    assert "www.sec.gov/Archives/edgar/data" in out  # real source_url() citation format


def test_with_piotroski_flag_also_prints_f_score(monkeypatch, capsys):
    monkeypatch.setattr(cli, "SecEdgarClient", _FixtureBackedClient)

    exit_code = cli.main(
        [
            "--cik", "0000789019",
            "--entity-name", "Microsoft Corporation",
            "--fiscal-year", "2024",
            "--with-piotroski",
        ]
    )

    out = capsys.readouterr().out
    # FY2024 in this fixture genuinely lacks complete Altman-relevant data
    # (see tests/unit/test_xbrl_facts.py) but does have everything
    # Piotroski needs — so this is a real, honest case of one score
    # succeeding while the other correctly reports it cannot be computed,
    # and the nonzero exit code reflects that Altman failure.
    assert exit_code == 1
    assert "Altman Z'-Score: CANNOT BE COMPUTED" in out
    assert "Piotroski F-Score: 5/9" in out


def test_insufficient_data_reports_cleanly_with_nonzero_exit(monkeypatch, capsys):
    # Real Apple data with the equity tags deliberately hidden (same
    # approach as tests/conftest.py::TagHidingClient): Apple's real FY2025
    # 10-K has every Altman input, so the gap must be simulated to exercise
    # the CLI's insufficient-data path.
    class _AaplFixtureClient(_FixtureBackedClient):
        def __init__(self, *_args, **_kwargs):
            with open(FIXTURES_DIR / "aapl_fy2025_companyconcept.json") as f:
                self._data = json.load(f)
            for tag in ("RetainedEarningsAccumulatedDeficit", "StockholdersEquity"):
                self._data.pop(tag, None)

    monkeypatch.setattr(cli, "SecEdgarClient", _AaplFixtureClient)

    exit_code = cli.main(
        ["--cik", "0000320193", "--entity-name", "Apple Inc.", "--fiscal-year", "2025"]
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "CANNOT BE COMPUTED" in out
    assert "DATA QUALITY ISSUES" in out


def test_ticker_lookup_failure_is_reported_and_does_not_crash(monkeypatch, capsys):
    def _raise(*_args, **_kwargs):
        raise TickerLookupError("unknown ticker ZZZZ")

    monkeypatch.setattr(cli, "resolve_ticker", _raise)

    exit_code = cli.main(["--ticker", "ZZZZ", "--fiscal-year", "2025"])

    assert exit_code == 1
    assert "TICKER LOOKUP ERROR" in capsys.readouterr().err


def test_save_flag_persists_to_the_given_db_path(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "SecEdgarClient", _FixtureBackedClient)
    db_path = str(tmp_path / "cli_test.db")

    exit_code = cli.main(
        [
            "--cik", "0000789019",
            "--entity-name", "Microsoft Corporation",
            "--fiscal-year", "2025",
            "--save",
            "--db-path", db_path,
        ]
    )

    assert exit_code == 0
    assert Path(db_path).exists()

    from src.persistence.storage import connect, get_altman_history

    conn = connect(db_path)
    try:
        history = get_altman_history(conn, "0000789019")
    finally:
        conn.close()
    assert len(history) == 1
    assert history[0]["fiscal_year"] == 2025


def test_save_flag_with_piotroski_also_persists_the_prior_years_real_facts(monkeypatch, tmp_path):
    """Regression test for a real defect: `--with-piotroski` already fetches
    a real prior fiscal year's snapshot from SEC EDGAR to compute the
    year-over-year comparison, but `--save` used to persist only the
    current fiscal year — silently discarding a year of real, already-
    fetched SEC data instead of storing it, which left this company with
    only one real fiscal year on record even after a two-year fetch."""
    monkeypatch.setattr(cli, "SecEdgarClient", _FixtureBackedClient)
    db_path = str(tmp_path / "cli_test.db")

    exit_code = cli.main(
        [
            "--cik", "0000789019",
            "--entity-name", "Microsoft Corporation",
            "--fiscal-year", "2024",
            "--with-piotroski",
            "--save",
            "--db-path", db_path,
        ]
    )

    # FY2024 genuinely has no computable Altman score in this fixture (see
    # test_analyze_company_reports_insufficient_data_honestly_not_as_a_crash)
    # — that honest failure is orthogonal to this test's actual point
    # (prior-year persistence), so it's asserted as-is rather than avoided.
    assert exit_code == 1

    from src.persistence.storage import connect, get_all_fiscal_years

    conn = connect(db_path)
    try:
        assert get_all_fiscal_years(conn, "0000789019") == [2023, 2024]
    finally:
        conn.close()
