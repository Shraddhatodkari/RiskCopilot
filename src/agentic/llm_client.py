"""
LLM client abstraction for the Phase 3 qualitative narrative layer.

Default real client (ADR-011): `OllamaLLMClient`, talking to a locally
running Ollama server (https://ollama.com). This is deliberately the
project's default "real" LLM path — zero API cost, no API key, and the
filing text/prompts never leave the user's machine — chosen over a paid
cloud LLM specifically so the qualitative layer can be run for free by
anyone with Ollama installed. `AnthropicLLMClient` is kept in this file as
an alternative pluggable implementation of the same `LLMClient` protocol
(useful if a user prefers a hosted model later) but is not constructed or
called anywhere in this project by default.

Honesty note (read before trusting any narrative this module produces):
this cloud development sandbox has no local Ollama server reachable from
it (Ollama runs on the *user's own* machine, not in this container — see
docs/02_architecture_decision_record.md, ADR-011, and
docs/07_security_review.md), so no live model call has been made *during
this project's own development*. `OllamaLLMClient`'s HTTP request/response
handling has been verified end-to-end against a real local HTTP server
speaking Ollama's actual `/api/chat` contract (see
tests/unit/test_ollama_client.py and scripts/verify_ollama_connection.py)
but that is not the same as a verified real-model output. Every example
narrative in this repository's docs is explicitly labeled ILLUSTRATIVE /
MOCK unless it was captured by actually running
`scripts/verify_ollama_connection.py` against a real local Ollama
instance, per the project's rule against fabricating results. What IS
real and tested without needing Ollama running at all: the `FakeLLMClient`
test double, and — more importantly — the deterministic grounding critic
in `src/agentic/critic.py`, which is what actually protects this system
from an ungrounded LLM claim reaching an analyst, regardless of which
concrete `LLMClient` produced the claim.

The `LLMClient` protocol is intentionally minimal (one method, plain
strings in and out) so the critic and narrative-assembly logic can be
fully unit-tested against `FakeLLMClient` without touching the network,
and so swapping providers (Ollama, Anthropic, or anything else) only
requires a new class implementing this one method.
"""
from __future__ import annotations

import os
from typing import Protocol

import requests


class LLMClient(Protocol):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's raw text completion."""
        ...


class OllamaError(RuntimeError):
    """Base class for OllamaLLMClient failures."""


class OllamaConnectionError(OllamaError):
    """Could not reach the local Ollama server at all (it's likely not
    running). Distinguished from other OllamaError cases so callers (e.g.
    the dashboard) can show a specific, actionable message rather than a
    generic failure."""


class OllamaLLMClient:
    """Real LLM client (ADR-011): calls a locally running Ollama server's
    `/api/chat` endpoint. Zero cost, no API key — the only requirement is
    that Ollama is installed and running on the same machine this code
    runs on, with the requested model already pulled (`ollama pull
    <model>`).

    Configuration (all optional; sensible free-local defaults, no .env
    entry required to get started):
      - `model`: defaults to the `OLLAMA_MODEL` env var, else
        "llama3.2:latest".
      - `base_url`: defaults to the `OLLAMA_BASE_URL` env var, else
        "http://localhost:11434" (Ollama's own default).
      - `timeout`: defaults to the `OLLAMA_TIMEOUT_SECONDS` env var, else
        300.0 seconds. Raised from an earlier 120.0s default: on a
        CPU-only machine, the *first* request after `ollama serve` starts
        has to load the model's weights into RAM before it can generate a
        single token, and for a several-GB model on a slow disk/CPU that
        alone can take well past two minutes — a real, observed failure
        mode (`Ollama request ... timed out after 120.0s`), not a bug in
        the request handling itself (verified against a real local HTTP
        server — see the test referenced below). 300s gives normal
        CPU-only generation of a short (≤4 paragraph) narrative real
        headroom without masking a genuinely unreachable server (that
        fails fast with `OllamaConnectionError`, independent of this
        timeout). Still fully overridable per call or via the env var if
        a given machine needs more or less.
      - `keep_alive`: defaults to the `OLLAMA_KEEP_ALIVE` env var, else
        "30m". Passed straight through to Ollama's own `/api/chat`
        `keep_alive` field so the model stays loaded in memory between
        calls instead of being evicted after each one — this is what
        keeps the *second and later* narrative generations in a dashboard
        session fast, since only the first call pays the load-time cost
        above. Purely a request parameter Ollama already supports; no new
        moving parts on this project's side.

    Verified: request/response handling against a real local HTTP server
    implementing Ollama's actual response shape
    (tests/unit/test_ollama_client.py::test_generate_against_a_real_local_http_server_speaking_ollamas_api).
    Not verified from within this project's own development sandbox: the
    quality of real llama3.2 output, because this cloud sandbox cannot
    reach a server on the user's own machine. Run
    `python scripts/verify_ollama_connection.py` on your own machine (with
    `ollama serve` running) to check that for real.
    """

    DEFAULT_BASE_URL = "http://localhost:11434"
    DEFAULT_MODEL = "llama3.2:latest"
    DEFAULT_TIMEOUT = 300.0
    DEFAULT_KEEP_ALIVE = "30m"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        keep_alive: str | None = None,
        session: "requests.Session | None" = None,
    ):
        self._model = model or os.environ.get("OLLAMA_MODEL", self.DEFAULT_MODEL)
        self._base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        if timeout is not None:
            self._timeout = timeout
        else:
            self._timeout = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", self.DEFAULT_TIMEOUT))
        self._keep_alive = keep_alive or os.environ.get("OLLAMA_KEEP_ALIVE", self.DEFAULT_KEEP_ALIVE)
        self._session = session or requests.Session()

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self._base_url}/api/chat"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "keep_alive": self._keep_alive,
        }
        try:
            response = self._session.post(url, json=payload, timeout=self._timeout)
        except requests.exceptions.ConnectionError as exc:
            raise OllamaConnectionError(
                f"Could not reach Ollama at {self._base_url}. Is Ollama running? "
                f"Start it with `ollama serve` (or open the Ollama desktop app), "
                f"and make sure the model is pulled first: `ollama pull {self._model}`. "
                f"Original error: {exc}"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise OllamaError(
                f"Ollama request to {url} timed out after {self._timeout}s. The most "
                f"common real cause is the model still loading into RAM on a "
                f"CPU-only machine (only the first call after `ollama serve` pays "
                f"this cost — set OLLAMA_KEEP_ALIVE, already defaulted to "
                f"'{self.DEFAULT_KEEP_ALIVE}', to keep it warm afterward). Set "
                f"OLLAMA_TIMEOUT_SECONDS (or pass `timeout=`) to raise this further, "
                f"or point OLLAMA_MODEL at a smaller already-pulled model if this "
                f"machine can't run '{self._model}' in reasonable time. "
                f"Original error: {exc}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise OllamaError(f"Request to {url} failed: {exc}") from exc

        if response.status_code == 404:
            raise OllamaError(
                f"Ollama returned 404 for model '{self._model}'. Has it been "
                f"pulled? Run: ollama pull {self._model}"
            )
        if response.status_code != 200:
            raise OllamaError(
                f"Ollama returned HTTP {response.status_code} from {url}: {response.text[:500]}"
            )

        try:
            data = response.json()
            return data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise OllamaError(f"Unexpected Ollama response shape: {response.text[:500]}") from exc

    @classmethod
    def is_reachable(cls, base_url: str | None = None, timeout: float = 3.0) -> bool:
        """Cheap liveness check (GET /api/tags) — used by the dashboard to
        decide whether to attempt a real call or show a clear
        not-running message instead of hanging/erroring mid-render."""
        url = (base_url or os.environ.get("OLLAMA_BASE_URL", cls.DEFAULT_BASE_URL)).rstrip("/") + "/api/tags"
        try:
            response = requests.get(url, timeout=timeout)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    @classmethod
    def list_models(cls, base_url: str | None = None, timeout: float = 3.0) -> list[str]:
        """Real model names currently pulled/available on the local Ollama
        server, via GET /api/tags. Returns [] if Ollama isn't reachable —
        callers should check `is_reachable` first if they need to
        distinguish "not running" from "running but empty"."""
        url = (base_url or os.environ.get("OLLAMA_BASE_URL", cls.DEFAULT_BASE_URL)).rstrip("/") + "/api/tags"
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return [m["name"] for m in response.json().get("models", [])]
        except (requests.exceptions.RequestException, ValueError, KeyError):
            return []


class AnthropicLLMClient:
    """Real implementation, backed by the user's own Anthropic API key.

    Not exercised by this project's automated tests (no key is available
    in the development sandbox) — see the module docstring. A user with
    their own key can smoke-test it directly:

        python -c "
        from src.config import load_settings
        from src.agentic.llm_client import AnthropicLLMClient
        c = AnthropicLLMClient(load_settings().anthropic_api_key)
        print(c.generate('You are terse.', 'Say hello in five words.'))
        "
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5"):
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is required to use AnthropicLLMClient. "
                "Set it in your .env file — see .env.example."
            )
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for AnthropicLLMClient. "
                "Install it with: pip install anthropic"
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(block.text for block in response.content if hasattr(block, "text"))


class FakeLLMClient:
    """Deterministic test double: returns a pre-scripted response (or
    cycles through a list of them), and records every prompt it was given
    so tests can assert on exactly what the narrative layer sent it."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if not self._responses:
            raise RuntimeError("FakeLLMClient has no more scripted responses")
        return self._responses.pop(0)
