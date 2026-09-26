"""
Tests for OllamaLLMClient (ADR-011: the project's default, free, local
real-LLM backend).

This project's own cloud development sandbox has no local Ollama server
reachable from it (Ollama runs on the *user's own* machine — see
src/agentic/llm_client.py's module docstring), so these tests cannot call
a real llama3.2 model. What they CAN and DO verify for real:

  1. Request construction and response parsing against a scripted fake
     `requests.Session` (same pattern as
     tests/unit/test_sec_edgar_client_retry.py) — fast, deterministic,
     no sockets.
  2. The full HTTP client, over a REAL socket, against a tiny local HTTP
     server (`http.server`, started in this test file) that implements
     Ollama's actual `/api/chat` JSON contract. This proves
     `OllamaLLMClient`'s request formatting, timeout handling, and
     response parsing all work correctly end-to-end at the HTTP layer —
     real network I/O, just not a real language model on the other end.

Both are real, useful verification; neither claims to be "we called your
llama3.2 and it produced a good answer" — that claim can only be verified
by running `python scripts/verify_ollama_connection.py` on a machine that
actually has Ollama running, which this test suite cannot do and does not
pretend to.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

from src.agentic.llm_client import OllamaConnectionError, OllamaError, OllamaLLMClient


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text or json.dumps(self._json_data)

    def json(self):
        return self._json_data


class _ScriptedSession:
    """Same pattern as tests/unit/test_sec_edgar_client_retry.py's
    _ScriptedSession: records every call, returns/raises scripted results."""

    def __init__(self, script: list):
        self._script = list(script)
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if not self._script:
            raise AssertionError("no more scripted responses")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# --- Fast, scripted-session tests (no real network) -------------------------


def test_sends_system_and_user_prompt_as_chat_messages():
    session = _ScriptedSession(
        [_FakeResponse(200, {"message": {"role": "assistant", "content": "hello"}})]
    )
    client = OllamaLLMClient(model="llama3.2:latest", base_url="http://localhost:11434", session=session)

    result = client.generate("You are terse.", "Say hi.")

    assert result == "hello"
    assert len(session.calls) == 1
    sent = session.calls[0]["json"]
    assert sent["model"] == "llama3.2:latest"
    assert sent["stream"] is False
    assert sent["messages"] == [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Say hi."},
    ]
    assert session.calls[0]["url"] == "http://localhost:11434/api/chat"



def test_requests_an_explicit_context_window(monkeypatch):
    """Without num_ctx, Ollama uses its version-dependent default (2048 on
    older releases) and truncates an overflowing prompt from the START,
    dropping the system rules. The client must always send num_ctx."""
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    session = _ScriptedSession([_FakeResponse(200, {"message": {"content": "ok"}})])
    OllamaLLMClient(session=session).generate("sys", "user")
    assert session.calls[0]["json"]["options"]["num_ctx"] == OllamaLLMClient.DEFAULT_NUM_CTX

    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    session = _ScriptedSession([_FakeResponse(200, {"message": {"content": "ok"}})])
    OllamaLLMClient(session=session).generate("sys", "user")
    assert session.calls[0]["json"]["options"]["num_ctx"] == 8192

def test_connection_error_is_wrapped_with_actionable_message():
    session = _ScriptedSession([requests.exceptions.ConnectionError("refused")])
    client = OllamaLLMClient(session=session)

    with pytest.raises(OllamaConnectionError) as exc_info:
        client.generate("sys", "user")
    assert "ollama serve" in str(exc_info.value)
    assert "ollama pull" in str(exc_info.value)


def test_timeout_raises_ollama_error_not_connection_error():
    session = _ScriptedSession([requests.exceptions.Timeout("slow")])
    client = OllamaLLMClient(session=session, timeout=5.0)

    with pytest.raises(OllamaError) as exc_info:
        client.generate("sys", "user")
    assert not isinstance(exc_info.value, OllamaConnectionError)
    assert "timed out" in str(exc_info.value)


def test_404_names_the_missing_model_and_the_fix():
    session = _ScriptedSession([_FakeResponse(404, text="model not found")])
    client = OllamaLLMClient(model="nonexistent-model", session=session)

    with pytest.raises(OllamaError) as exc_info:
        client.generate("sys", "user")
    assert "nonexistent-model" in str(exc_info.value)
    assert "ollama pull nonexistent-model" in str(exc_info.value)


def test_non_200_non_404_status_raises_with_body_excerpt():
    session = _ScriptedSession([_FakeResponse(500, text="internal server error")])
    client = OllamaLLMClient(session=session)

    with pytest.raises(OllamaError) as exc_info:
        client.generate("sys", "user")
    assert "500" in str(exc_info.value)


def test_malformed_response_shape_raises_clearly():
    session = _ScriptedSession([_FakeResponse(200, {"unexpected": "shape"})])
    client = OllamaLLMClient(session=session)

    with pytest.raises(OllamaError, match="Unexpected Ollama response shape"):
        client.generate("sys", "user")


def test_model_and_base_url_default_from_env_vars(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "mistral:latest")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.50:11434")
    session = _ScriptedSession(
        [_FakeResponse(200, {"message": {"role": "assistant", "content": "ok"}})]
    )
    client = OllamaLLMClient(session=session)

    client.generate("sys", "user")

    assert session.calls[0]["url"] == "http://192.168.1.50:11434/api/chat"
    assert session.calls[0]["json"]["model"] == "mistral:latest"


def test_constructor_args_override_env_vars(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "mistral:latest")
    session = _ScriptedSession(
        [_FakeResponse(200, {"message": {"role": "assistant", "content": "ok"}})]
    )
    client = OllamaLLMClient(model="llama3.2:latest", session=session)

    client.generate("sys", "user")

    assert session.calls[0]["json"]["model"] == "llama3.2:latest"


def test_base_url_trailing_slash_is_normalized():
    session = _ScriptedSession(
        [_FakeResponse(200, {"message": {"role": "assistant", "content": "ok"}})]
    )
    client = OllamaLLMClient(base_url="http://localhost:11434/", session=session)

    client.generate("sys", "user")

    assert session.calls[0]["url"] == "http://localhost:11434/api/chat"


def test_is_reachable_false_when_nothing_is_listening():
    # Port 1 is a real, always-refused port (privileged, nothing binds it in
    # a test sandbox) — a genuine "not running" case, not mocked.
    assert OllamaLLMClient.is_reachable(base_url="http://localhost:1", timeout=1.0) is False


def test_list_models_empty_when_unreachable():
    assert OllamaLLMClient.list_models(base_url="http://localhost:1", timeout=1.0) == []


# --- Real end-to-end HTTP test against a live local server -----------------


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    """Implements just enough of Ollama's real /api/chat and /api/tags
    contract to prove OllamaLLMClient's HTTP layer works over a real
    socket, not just against a Python mock object."""

    def log_message(self, *_args):
        pass  # silence test output

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        assert body["stream"] is False
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"

        reply = {
            "model": body["model"],
            "message": {
                "role": "assistant",
                "content": f"[fake local model echo] you said: {body['messages'][1]['content']}",
            },
            "done": True,
        }
        payload = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        payload = json.dumps({"models": [{"name": "llama3.2:latest"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def real_local_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_generate_against_a_real_local_http_server_speaking_ollamas_api(real_local_server):
    """Real socket, real HTTP request/response — not a mocked Python
    object. This is the strongest verification this test suite can offer
    without an actual Ollama installation (see module docstring)."""
    client = OllamaLLMClient(model="llama3.2:latest", base_url=real_local_server, timeout=10.0)

    result = client.generate("You are a helpful analyst.", "Summarize the risk.")

    assert result == "[fake local model echo] you said: Summarize the risk."


def test_is_reachable_true_against_a_real_local_server(real_local_server):
    assert OllamaLLMClient.is_reachable(base_url=real_local_server, timeout=5.0) is True


def test_list_models_against_a_real_local_server(real_local_server):
    assert OllamaLLMClient.list_models(base_url=real_local_server, timeout=5.0) == ["llama3.2:latest"]
