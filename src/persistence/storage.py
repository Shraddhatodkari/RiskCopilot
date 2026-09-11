"""
SQLite persistence for ingested facts and computed scores (ADR-007: SQLite
over a client/server database — a single-analyst local tool has no
concurrent-writer problem to solve).

Every write uses parameterized queries (`?` placeholders) — never string-
formatted SQL — which is both the correct default and this project's
concrete defense against SQL injection (see docs/07_security_review.md).

Schema design choice: `fact_points` stores one row per (company, concept,
fiscal_year, accession_number) so that if a filing is amended (a 10-K/A),
the amended figure is stored as a new row rather than overwriting history —
an analyst reviewing why a score changed can see both the original and
restated figures. `INSERT OR REPLACE` on the full natural key makes
re-running ingestion for the same filing idempotent (no duplicate rows on
repeat runs) without needing a separate "does this already exist" check.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src.analysis.altman_z import ZScoreResult
from src.analysis.piotroski import PiotroskiResult
from src.ingestion.models import FactPoint, FinancialSnapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cik TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    concept TEXT NOT NULL,
    tag_used TEXT NOT NULL,
    taxonomy TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_period TEXT NOT NULL,
    form TEXT NOT NULL,
    filed TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cik, concept, fiscal_year, accession_number)
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cik TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    concept TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS altman_z_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cik TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    z_score REAL NOT NULL,
    zone TEXT NOT NULL,
    components_json TEXT NOT NULL,
    computed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cik, fiscal_year)
);

CREATE TABLE IF NOT EXISTS piotroski_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cik TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    prior_fiscal_year INTEGER NOT NULL,
    f_score INTEGER NOT NULL,
    signals_json TEXT NOT NULL,
    computed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cik, fiscal_year)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def open_db(db_path: str):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_snapshot(conn: sqlite3.Connection, snapshot: FinancialSnapshot) -> None:
    """Persist every real, sourced fact in a snapshot, plus any recorded
    data-quality issues, so a historical time series can be built up
    without re-fetching unchanged filings.

    Data-quality "current state" semantics (ADR-012): every call to this
    function for a given (cik, fiscal_year) first DELETES that pair's prior
    `data_quality_issues` rows, then inserts exactly the issues from *this*
    run. This is a deliberate honesty fix, not an incidental detail: before
    it, re-running ingestion for the same company/year (e.g. re-running
    `scripts/seed_dashboard_data.py`, or the CLI with `--save` more than
    once) silently accumulated duplicate issue rows forever, and a concept
    that later became available (e.g. after this project added a new tag
    fallback) would still show its old "missing" issue sitting next to
    fresh facts — a stale warning presented as current. With delete-then-
    insert, the table always reflects only the most recent ingestion run's
    actual findings: a concept with no issue row is either present or was
    never checked as a required concept; a concept's issue disappearing
    across two runs means it was genuinely resolved (see
    tests/unit/test_storage.py::test_resolved_data_quality_issue_does_not_persist_as_stale).
    """
    conn.execute(
        "DELETE FROM data_quality_issues WHERE cik = ? AND fiscal_year = ?",
        (snapshot.cik, snapshot.fiscal_year),
    )

    for fact in snapshot.all_facts().values():
        if fact is None:
            continue
        _save_fact(conn, snapshot.cik, snapshot.entity_name, fact)

    for issue in snapshot.data_quality_issues:
        conn.execute(
            """INSERT INTO data_quality_issues
               (cik, fiscal_year, concept, severity, detail)
               VALUES (?, ?, ?, ?, ?)""",
            (snapshot.cik, snapshot.fiscal_year, issue.concept, issue.severity, issue.detail),
        )


def _save_fact(conn: sqlite3.Connection, cik: str, entity_name: str, fact: FactPoint) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO fact_points
           (cik, entity_name, concept, tag_used, taxonomy, value, unit,
            period_start, period_end, fiscal_year, fiscal_period, form,
            filed, accession_number)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cik,
            entity_name,
            fact.concept,
            fact.tag_used,
            fact.taxonomy,
            fact.value,
            fact.unit,
            fact.period_start.isoformat() if fact.period_start else None,
            fact.period_end.isoformat(),
            fact.fiscal_year,
            fact.fiscal_period,
            fact.form,
            fact.filed.isoformat(),
            fact.accession_number,
        ),
    )


def save_altman_result(conn: sqlite3.Connection, result: ZScoreResult) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO altman_z_scores
           (cik, entity_name, fiscal_year, z_score, zone, components_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            result.cik,
            result.entity_name,
            result.fiscal_year,
            result.z_score,
            result.zone.value,
            json.dumps([c.model_dump() for c in result.components]),
        ),
    )


def save_piotroski_result(conn: sqlite3.Connection, result: PiotroskiResult) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO piotroski_scores
           (cik, entity_name, fiscal_year, prior_fiscal_year, f_score, signals_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            result.cik,
            result.entity_name,
            result.fiscal_year,
            result.prior_fiscal_year,
            result.f_score,
            json.dumps([s.model_dump() for s in result.signals]),
        ),
    )


def invalidate_altman_result(conn: sqlite3.Connection, cik: str, fiscal_year: int, reason: str) -> None:
    """Derived-data lifecycle fix (ADR-013 — the "stale-derived-score" bug):
    delete any PREVIOUSLY stored Altman result for this exact (cik,
    fiscal_year) and record why, so a once-valid score can never keep
    appearing as current once a fresh recomputation attempt for that same
    period genuinely fails.

    Call this whenever a real recomputation attempt is made for a
    (cik, fiscal_year) and `compute_altman_z_prime` raises
    `InsufficientDataError` — see `src/reporting/live_ingest.py` and
    `src/cli.py`'s `--save` path, both of which call this instead of
    silently leaving an old row untouched. A period that was never
    computable in the first place is unaffected (the DELETE is a no-op;
    nothing to invalidate), so this never fabricates history for a period
    that was always missing data — it only prevents a score that WAS once
    real from outliving the data that supported it.

    See tests/unit/test_storage.py::test_stale_altman_score_is_invalidated_
    when_recomputation_fails for the exact "valid score exists -> source
    becomes insufficient -> old score gone -> DQ issue recorded" regression
    this closes.
    """
    conn.execute("DELETE FROM altman_z_scores WHERE cik = ? AND fiscal_year = ?", (cik, fiscal_year))
    conn.execute(
        """INSERT INTO data_quality_issues (cik, fiscal_year, concept, severity, detail)
           VALUES (?, ?, 'altman_z_score', 'invalidated', ?)""",
        (cik, fiscal_year, reason),
    )


def invalidate_piotroski_result(conn: sqlite3.Connection, cik: str, fiscal_year: int, reason: str) -> None:
    """Same derived-data lifecycle guarantee as `invalidate_altman_result`,
    for the Piotroski F-Score."""
    conn.execute("DELETE FROM piotroski_scores WHERE cik = ? AND fiscal_year = ?", (cik, fiscal_year))
    conn.execute(
        """INSERT INTO data_quality_issues (cik, fiscal_year, concept, severity, detail)
           VALUES (?, ?, 'piotroski_f_score', 'invalidated', ?)""",
        (cik, fiscal_year, reason),
    )


def get_altman_history(conn: sqlite3.Connection, cik: str) -> list[dict]:
    """All stored Altman Z' results for a company, oldest fiscal year
    first — the time series a dashboard trend chart needs."""
    rows = conn.execute(
        """SELECT fiscal_year, z_score, zone, computed_at FROM altman_z_scores
           WHERE cik = ? ORDER BY fiscal_year ASC""",
        (cik,),
    ).fetchall()
    return [
        {"fiscal_year": r[0], "z_score": r[1], "zone": r[2], "computed_at": r[3]} for r in rows
    ]


def get_piotroski_history(conn: sqlite3.Connection, cik: str) -> list[dict]:
    rows = conn.execute(
        """SELECT fiscal_year, f_score, computed_at FROM piotroski_scores
           WHERE cik = ? ORDER BY fiscal_year ASC""",
        (cik,),
    ).fetchall()
    return [{"fiscal_year": r[0], "f_score": r[1], "computed_at": r[2]} for r in rows]


def get_latest_altman(conn: sqlite3.Connection, cik: str) -> dict | None:
    """The most recent stored Altman result for a company, WITH its full
    5-component breakdown decoded from `components_json` — what a
    reviewer needs to actually audit how the score was calculated (each
    component's formula and value), not just the final number."""
    row = conn.execute(
        """SELECT fiscal_year, z_score, zone, components_json, computed_at
           FROM altman_z_scores WHERE cik = ? ORDER BY fiscal_year DESC LIMIT 1""",
        (cik,),
    ).fetchone()
    if row is None:
        return None
    return {
        "fiscal_year": row[0],
        "z_score": row[1],
        "zone": row[2],
        "components": json.loads(row[3]),
        "computed_at": row[4],
    }


def get_latest_piotroski(conn: sqlite3.Connection, cik: str) -> dict | None:
    """The most recent stored Piotroski result, WITH all 9 signals decoded
    from `signals_json` (name, passed, description) for full auditability."""
    row = conn.execute(
        """SELECT fiscal_year, prior_fiscal_year, f_score, signals_json, computed_at
           FROM piotroski_scores WHERE cik = ? ORDER BY fiscal_year DESC LIMIT 1""",
        (cik,),
    ).fetchone()
    if row is None:
        return None
    return {
        "fiscal_year": row[0],
        "prior_fiscal_year": row[1],
        "f_score": row[2],
        "signals": json.loads(row[3]),
        "computed_at": row[4],
    }


def get_facts(conn: sqlite3.Connection, cik: str, fiscal_year: int) -> list[dict]:
    """Every stored, sourced fact for one company/fiscal year, with full
    provenance — the raw material a reviewer traces a score back to
    (concept -> tag -> filing -> accession -> value)."""
    rows = conn.execute(
        """SELECT concept, tag_used, taxonomy, value, unit, period_end,
                  fiscal_period, form, filed, accession_number
           FROM fact_points WHERE cik = ? AND fiscal_year = ?
           ORDER BY concept""",
        (cik, fiscal_year),
    ).fetchall()
    return [
        {
            "concept": r[0], "tag_used": r[1], "taxonomy": r[2], "value": r[3],
            "unit": r[4], "period_end": r[5], "fiscal_period": r[6],
            "form": r[7], "filed": r[8], "accession_number": r[9],
        }
        for r in rows
    ]


def get_filing_metadata(conn: sqlite3.Connection, cik: str, fiscal_year: int) -> dict | None:
    """The filing (form/filed date/accession) that this fiscal year's
    stored facts actually came from — derived from `fact_points` rather
    than tracked separately, so it can never drift out of sync with the
    facts it describes. Returns None if no facts are stored for this
    (cik, fiscal_year)."""
    row = conn.execute(
        """SELECT form, filed, accession_number, COUNT(*) as n
           FROM fact_points WHERE cik = ? AND fiscal_year = ?
           GROUP BY form, filed, accession_number
           ORDER BY n DESC LIMIT 1""",
        (cik, fiscal_year),
    ).fetchone()
    if row is None:
        return None
    return {"form": row[0], "filed": row[1], "accession_number": row[2], "fact_count": row[3]}


def get_latest_known_fiscal_year(conn: sqlite3.Connection, cik: str) -> int | None:
    """The most recent fiscal year this project has ANY stored data for a
    company — a stored fact, a recorded data-quality issue, or a computed
    score. Deliberately broader than "has a computed score", because a
    company can have a completely genuine, honestly-reported fiscal year
    with real ingested facts and real data-quality issues but NO
    computable score at all (e.g. Apple FY2025 in this project's own seed
    data, which is missing retained_earnings) — that year is still the
    company's "current" year for dashboard purposes, and its issues must
    not be silently dropped just because no score exists to hang them off
    of. Returns None only if this company has no data at all."""
    rows = conn.execute(
        """SELECT MAX(fy) FROM (
               SELECT fiscal_year AS fy FROM fact_points WHERE cik = ?
               UNION ALL
               SELECT fiscal_year AS fy FROM data_quality_issues WHERE cik = ?
               UNION ALL
               SELECT fiscal_year AS fy FROM altman_z_scores WHERE cik = ?
               UNION ALL
               SELECT fiscal_year AS fy FROM piotroski_scores WHERE cik = ?
           )""",
        (cik, cik, cik, cik),
    ).fetchone()
    return rows[0] if rows and rows[0] is not None else None


def list_companies(conn: sqlite3.Connection) -> list[dict]:
    """Distinct (cik, entity_name) pairs with ANY stored data — a computed
    score, OR real ingested facts/data-quality issues even when no score
    could be computed at all. This last case matters honestly: a company
    whose scoring genuinely failed (e.g. a required concept is missing) is
    still real, ingested, worth showing, and must not disappear from the
    picker just because it has no score — see ADR-012 and
    src.reporting.company_dossier for how that state is then displayed
    (an explicit 'no score computable' state, never a placeholder score)."""
    rows = conn.execute(
        """SELECT DISTINCT cik, entity_name FROM altman_z_scores
           UNION
           SELECT DISTINCT cik, entity_name FROM piotroski_scores
           UNION
           SELECT DISTINCT cik, entity_name FROM fact_points
           ORDER BY entity_name"""
    ).fetchall()
    return [{"cik": r[0], "entity_name": r[1]} for r in rows]


def get_all_fiscal_years(conn: sqlite3.Connection, cik: str) -> list[int]:
    """Every distinct fiscal year this company has real, stored facts for,
    ascending — the span the historical-trend view (ADR-013) computes
    each ratio across. Deliberately reads `fact_points` (not the score
    tables), so a fiscal year with real facts but no computable score
    still contributes its available ratios to the trend, consistent with
    `get_latest_known_fiscal_year`'s same reasoning."""
    rows = conn.execute(
        "SELECT DISTINCT fiscal_year FROM fact_points WHERE cik = ? ORDER BY fiscal_year ASC",
        (cik,),
    ).fetchall()
    return [r[0] for r in rows]


def build_snapshot_from_stored_facts(
    conn: sqlite3.Connection, cik: str, entity_name: str, fiscal_year: int
) -> FinancialSnapshot:
    """Reconstruct a `FinancialSnapshot` for one already-ingested fiscal
    year purely from what is stored in `fact_points` — used by the
    historical-trend view to recompute ratios for PAST years without
    re-fetching them from SEC EDGAR (this project's own past ingestion
    runs already did that fetch once; re-deriving the same real, sourced
    facts from local storage is not fabrication, since every value here
    still carries its own real tag/accession provenance from
    `fact_points`). Concepts with no stored row for this (cik, fiscal_year)
    are left None, exactly as `build_financial_snapshot` would leave them
    for a concept genuinely missing from the filing — see
    src/analysis/financial_ratios.py's per-ratio INSUFFICIENT_DATA
    handling for how that's surfaced.
    """
    rows = conn.execute(
        """SELECT concept, tag_used, taxonomy, value, unit, period_start, period_end,
                  fiscal_period, form, filed, accession_number
           FROM fact_points WHERE cik = ? AND fiscal_year = ?""",
        (cik, fiscal_year),
    ).fetchall()

    snapshot = FinancialSnapshot(cik=cik, entity_name=entity_name, fiscal_year=fiscal_year)
    for r in rows:
        fact = FactPoint(
            concept=r[0], tag_used=r[1], taxonomy=r[2], value=r[3], unit=r[4],
            period_start=r[5], period_end=r[6], fiscal_year=fiscal_year,
            fiscal_period=r[7], form=r[8], filed=r[9], accession_number=r[10],
        )
        if hasattr(snapshot, fact.concept):
            setattr(snapshot, fact.concept, fact)
    return snapshot


def get_data_quality_issues(conn: sqlite3.Connection, cik: str, fiscal_year: int) -> list[dict]:
    rows = conn.execute(
        """SELECT concept, severity, detail FROM data_quality_issues
           WHERE cik = ? AND fiscal_year = ?""",
        (cik, fiscal_year),
    ).fetchall()
    return [{"concept": r[0], "severity": r[1], "detail": r[2]} for r in rows]
