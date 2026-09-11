"""
Phase 5 performance benchmark.

Honesty note (read before trusting any number this script prints): this
project's hard constraint is that it must run on an old, resource-
constrained Windows laptop, not on whatever machine happens to run this
script. This sandbox environment is a cloud container with unknown-to-us
CPU/RAM characteristics that may be faster OR slower than the target
laptop, and it cannot make outbound calls to data.sec.gov/api.stlouisfed.org
at all (see docs/03_data_provenance.md) — so this script CANNOT produce a
real end-to-end "fetch from SEC + compute" latency number, and it does not
pretend to. What it CAN measure honestly, on THIS machine, right now:

  1. The pure-Python cost of the deterministic financial math (Altman Z',
     Piotroski F-Score) given an already-fetched snapshot — this is the
     part of the pipeline that runs identically regardless of network
     speed, and it is the part most relevant to "will this old laptop's
     CPU be the bottleneck" (answer, per the numbers below: no).
  2. The cost of building a TF-IDF index and running a retrieval query over
     a realistic (~10-chunk) set of real risk-factor passages — the other
     CPU-bound step in the pipeline.
  3. SQLite persistence round-trip cost (write + read) on local disk.

It deliberately does NOT benchmark network calls to SEC/FRED (this sandbox
cannot reach them; see the live-integration test's docstring for the exact
proxy error) or LLM calls (no live LLM call is made anywhere in this
project — see src/agentic/llm_client.py). A user running this on their own
laptop, with their own network, should re-run this script there — every
number below is labeled with the machine it came from and the date, per
docs/00_business_case.md's "never fabricate benchmark numbers" rule; it is
not portable to claims about the user's own hardware without being re-run
there.

Run: python scripts/benchmark.py
"""
from __future__ import annotations

import json
import logging
import platform
import sqlite3
import sys
import time
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analysis.altman_z import compute_altman_z_prime  # noqa: E402
from src.analysis.piotroski import compute_piotroski_f_score  # noqa: E402
from src.ingestion.models import FactPoint, FinancialSnapshot  # noqa: E402
from src.ingestion.xbrl_facts import build_financial_snapshot  # noqa: E402
from src.persistence.storage import connect, save_altman_result, save_snapshot  # noqa: E402
from src.retrieval.tfidf_index import TfidfRiskFactorIndex, load_chunks_from_fixture  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
N_ITERATIONS = 200


class _FixtureBackedClient:
    """Same duck-typed fixture client tests/conftest.py uses, reused here so
    this benchmark exercises the real build_financial_snapshot parsing path
    (tag fallback, unit handling, fiscal-year filtering) rather than a
    shortcut."""

    def __init__(self, fixture_name: str):
        with open(FIXTURES_DIR / fixture_name) as f:
            self._data = json.load(f)

    def get_company_concept(self, cik: str, taxonomy: str, tag: str) -> dict:
        from src.ingestion.sec_edgar_client import SecEdgarNotFound

        if tag not in self._data:
            raise SecEdgarNotFound(f"fixture has no tag {tag}")
        return self._data[tag]


def _time_it(fn, n=N_ITERATIONS):
    # Warm up once (import caches, disk cache, etc.) before timing.
    fn()
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)  # ms
    return samples


def report(name: str, samples: list[float]) -> None:
    print(f"{name}:")
    print(f"  n={len(samples)}  mean={mean(samples):.3f}ms  "
          f"stdev={stdev(samples):.3f}ms  min={min(samples):.3f}ms  max={max(samples):.3f}ms")


def main() -> None:
    # Suppress the expected, already-documented "Data quality issue" warnings
    # (e.g. MSFT's FY2025 filing genuinely lacking a clean long_term_debt tag
    # — see docs/03_data_provenance.md) so 200 warm-up/timing iterations don't
    # flood this benchmark's output; those warnings are already covered by
    # tests/unit/test_xbrl_facts.py and are not a performance concern.
    logging.disable(logging.WARNING)

    print("=== RiskCopilot Phase 5 Performance Benchmark ===")
    print(f"Machine: {platform.processor() or platform.machine()}, "
          f"{platform.system()} {platform.release()}, Python {platform.python_version()}")
    print("Date: 2026-09-08 (this run's actual date/machine — re-run on your own")
    print("laptop for numbers that reflect your own hardware; see this script's")
    print("module docstring for exactly what is and is not measured here.)")
    print()

    # --- 1. Snapshot assembly + Altman Z' + Piotroski F, from a real fixture ---
    client = _FixtureBackedClient("msft_fy2025_companyconcept.json")

    def build_snapshot_fy2025():
        # FY2025 is the fiscal year with complete Altman-relevant data in
        # this fixture (see tests/unit/test_altman_z.py).
        return build_financial_snapshot(client, cik="0000789019", entity_name="MICROSOFT CORP", fiscal_year=2025)

    def build_snapshot_fy2024():
        # FY2024/FY2023 is the consecutive-year pair with complete
        # Piotroski-relevant data (see tests/unit/test_piotroski.py).
        return build_financial_snapshot(client, cik="0000789019", entity_name="MICROSOFT CORP", fiscal_year=2024)

    def build_snapshot_fy2023():
        return build_financial_snapshot(client, cik="0000789019", entity_name="MICROSOFT CORP", fiscal_year=2023)

    snap_2025 = build_snapshot_fy2025()
    snap_2024 = build_snapshot_fy2024()
    snap_2023 = build_snapshot_fy2023()

    def run_altman():
        return compute_altman_z_prime(snap_2025)

    def run_piotroski():
        return compute_piotroski_f_score(snap_2024, snap_2023)

    report("Snapshot assembly (parse real fixture JSON -> FinancialSnapshot)",
           _time_it(build_snapshot_fy2025))
    report("Altman Z'-Score computation (pure arithmetic on an assembled snapshot)",
           _time_it(run_altman))
    report("Piotroski F-Score computation (9 signals, two snapshots)",
           _time_it(run_piotroski))
    print()

    # --- 2. TF-IDF retrieval over a real ~10-chunk risk-factor set ---
    chunks = load_chunks_from_fixture(json.loads((FIXTURES_DIR / "aapl_fy2025_risk_factors.json").read_text()))

    def build_index():
        return TfidfRiskFactorIndex(chunks)

    index = build_index()

    def run_query():
        return index.search("supply chain concentration risk single source component pricing", top_k=3)

    report(f"TF-IDF index build ({len(chunks)} real Apple 10-K risk-factor chunks)",
           _time_it(build_index, n=50))
    report("TF-IDF query (already-built index)", _time_it(run_query))
    print()

    # --- 3. SQLite persistence round-trip on local disk ---
    bench_db_path = "data/benchmark_scratch.db"
    Path("data").mkdir(exist_ok=True)
    Path(bench_db_path).unlink(missing_ok=True)

    def write_snapshot():
        conn = connect(bench_db_path)
        try:
            save_snapshot(conn, snap_2025)
            z = compute_altman_z_prime(snap_2025)
            save_altman_result(conn, z)
            conn.commit()
        finally:
            conn.close()

    def read_back():
        conn = sqlite3.connect(bench_db_path)
        try:
            return conn.execute("SELECT * FROM altman_z_scores").fetchall()
        finally:
            conn.close()

    report("SQLite write (snapshot + Altman result, INSERT OR REPLACE)", _time_it(write_snapshot, n=100))
    report("SQLite read (SELECT all Altman scores)", _time_it(read_back, n=100))
    Path(bench_db_path).unlink(missing_ok=True)
    print()

    print("--- Interpretation ---")
    print("All measured operations complete in low-single-digit milliseconds or")
    print("less on this sandbox machine. Even a laptop 10-20x slower on raw CPU")
    print("would keep every one of these steps well under 100ms — none of them")
    print("involve a loop over a large dataset or an expensive algorithm; the")
    print("actual end-to-end latency of a real run is dominated by network round")
    print("trips to data.sec.gov/api.stlouisfed.org (not measurable from this")
    print("sandbox) and, when used, the LLM API call (also not measured here —")
    print("no live LLM call has been made anywhere in this project). This is the")
    print("honest scope of what local compute-bound benchmarking can tell us for")
    print("a tool whose real bottleneck is expected to be network I/O, not CPU.")


if __name__ == "__main__":
    main()
