"""
Run this ON THE MACHINE THAT HAS OLLAMA INSTALLED (your own laptop, not
this project's cloud development sandbox — see ADR-011 and
src/agentic/llm_client.py's module docstring for why that distinction
matters here).

This actually calls your real local Ollama server and your real
llama3.2:latest (or whichever model you name) — the one live-model check
this project's own automated test suite cannot perform, because it runs in
an environment that cannot reach your machine's localhost.

Usage:
    ollama serve                      # if not already running
    ollama pull llama3.2:latest       # if not already pulled
    python scripts/verify_ollama_connection.py
    python scripts/verify_ollama_connection.py --model mistral:latest
    python scripts/verify_ollama_connection.py --base-url http://localhost:11434
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agentic.critic import check_grounding  # noqa: E402
from src.agentic.llm_client import (  # noqa: E402
    OllamaConnectionError,
    OllamaError,
    OllamaLLMClient,
)
from src.agentic.narrative import draft_risk_narrative  # noqa: E402
from src.retrieval.tfidf_index import TfidfRiskFactorIndex, load_chunks_from_fixture  # noqa: E402

import json  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "aapl_fy2025_risk_factors.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=OllamaLLMClient.DEFAULT_MODEL)
    parser.add_argument("--base-url", default=OllamaLLMClient.DEFAULT_BASE_URL)
    parser.add_argument(
        "--timeout", type=float, default=OllamaLLMClient.DEFAULT_TIMEOUT,
        help=f"Seconds to wait for a response (default {OllamaLLMClient.DEFAULT_TIMEOUT}s — "
        f"a CPU-only machine's first call pays a real model-load cost).",
    )
    args = parser.parse_args()

    print(f"=== RiskCopilot: real Ollama connection check ===")
    print(f"Base URL: {args.base_url}")
    print(f"Model:    {args.model}")
    print()

    print("Step 1: checking Ollama is reachable (GET /api/tags)...")
    if not OllamaLLMClient.is_reachable(base_url=args.base_url, timeout=5.0):
        print(f"FAILED: could not reach Ollama at {args.base_url}.")
        print("Fix: run `ollama serve` (or open the Ollama app) and try again.")
        return 1
    print("OK — Ollama is running.")

    models = OllamaLLMClient.list_models(base_url=args.base_url)
    print(f"Models currently pulled: {models or '(none)'}")
    if args.model not in models:
        print(f"\nFAILED: '{args.model}' is not pulled on this server.")
        print(f"Fix: run `ollama pull {args.model}` and try again.")
        return 1
    print()

    print("Step 2: making a real, live call to the model...")
    print(f"(timeout: {args.timeout}s — raise with --timeout if this is the model's first load)")
    client = OllamaLLMClient(model=args.model, base_url=args.base_url, timeout=args.timeout)
    start = time.perf_counter()
    try:
        raw = client.generate(
            "You are a terse assistant.",
            "Reply with exactly the words: connection successful",
        )
    except (OllamaConnectionError, OllamaError) as exc:
        print(f"FAILED: {exc}")
        return 1
    elapsed = time.perf_counter() - start
    print(f"OK — model responded in {elapsed:.2f}s.")
    print(f"Raw response: {raw!r}")
    print()

    print("Step 3: running the real Phase 3 pipeline (retrieval -> real LLM -> grounding critic)...")
    chunks = load_chunks_from_fixture(json.loads(FIXTURE_PATH.read_text()))
    index = TfidfRiskFactorIndex(chunks)
    retrieved = index.search("supply chain concentration risk single source component pricing", top_k=3)
    metrics = {"altman_z_score": "2.01 (grey zone)", "piotroski_f_score": "5/9 (moderate)"}

    draft = draft_risk_narrative(client, metrics, retrieved)
    print("\n--- Real narrative from your local model (not scripted) ---")
    print(draft.narrative_text)
    print("--- end narrative ---\n")
    print(f"Grounding critic result: {'PASSED' if draft.critic_report.passed else 'FAILED'}")
    if not draft.critic_report.passed:
        print(f"  Ungrounded citations: {draft.critic_report.ungrounded_citations}")
        print(f"  Uncited numeric sentences: {draft.critic_report.uncited_numeric_sentences}")
        print(
            "\nNote: a FAILED critic result here is not a bug in this script — it means your "
            "local model didn't follow the citation format instructions this time (smaller "
            "local models are more prone to this than larger hosted ones). That's exactly "
            "the failure mode the critic + human-approval gate exist to catch before a memo "
            "like this would ever reach an analyst. Try again, or try a larger/instruction-"
            "tuned model."
        )

    print("\n=== All steps completed against a REAL local Ollama call. ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
