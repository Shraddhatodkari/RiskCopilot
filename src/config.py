"""
Centralized configuration.

Design decision (see docs/02_architecture_decision_record.md, ADR-003):
secrets and environment-specific values are never hard-coded. They are read
from environment variables (optionally loaded from a local .env file that is
git-ignored). Missing required configuration fails loudly and immediately
rather than silently falling back to a placeholder, because a silent
fallback here (e.g. a fake User-Agent) would risk getting the caller's IP
rate-limited or blocked by SEC EDGAR.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # python-dotenv is a convenience, not a hard requirement: config can also
    # be supplied via real environment variables (e.g. in CI).
    pass


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    sec_edgar_user_agent: str
    fred_api_key: str | None
    anthropic_api_key: str | None  # Optional alternative LLM backend — see
    # src/agentic/llm_client.py's AnthropicLLMClient. Not used by default
    # anywhere in this project (ADR-011: Ollama is the default real client).
    ollama_base_url: str = "http://localhost:11434"  # Ollama's own default;
    # no env var needed unless Ollama runs somewhere non-default.
    ollama_model: str = "llama3.2:latest"  # ADR-011: the project's default
    # real, zero-cost LLM for the Phase 3 narrative layer. Override via
    # OLLAMA_MODEL if you've pulled a different local model.
    sec_requests_per_second: float = 4.0  # SEC allows 10/s; we default to a
    # conservative 4/s (well under SEC's own "target 8/s" guidance) because
    # this is a single-analyst research tool, not a bulk downloader — there
    # is no business reason to run close to the limit, and staying well
    # under it avoids ever tripping SEC's automated blocking.
    request_timeout_seconds: float = 30.0
    max_retries: int = 3
    db_path: str = "data/riskcopilot.db"


def load_settings() -> Settings:
    user_agent = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if not user_agent or "@" not in user_agent:
        raise ConfigError(
            "SEC_EDGAR_USER_AGENT is not set (or looks invalid). SEC EDGAR "
            "rejects requests without a descriptive User-Agent containing a "
            "real contact email — see https://www.sec.gov/os/webmaster-faq#developers. "
            "Copy .env.example to .env and set it before running ingestion."
        )
    return Settings(
        sec_edgar_user_agent=user_agent,
        fred_api_key=os.environ.get("FRED_API_KEY") or None,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434",
        ollama_model=os.environ.get("OLLAMA_MODEL") or "llama3.2:latest",
    )
