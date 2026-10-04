import os

# Before any app import: fake LLM, no tracing export. The repo-root .env is
# loaded by app.config (override=False), so the real key may end up in the
# environment; autouse fixtures below remove it so nothing can reach OpenAI.
os.environ["PLAYGROUND_FAKE_LLM"] = "1"
os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"

import copy  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402
from agents import set_tracing_disabled  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import create_app  # noqa: E402
from app.samples import SAMPLES  # noqa: E402
from app.schemas import RunRequest  # noqa: E402

TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config, "FAKE_LLM", True)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "runs.db")
    set_tracing_disabled(True)


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    # base_url host must pass TrustedHost ("testserver" would not)
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def sample_body(key: str = "A", instructions: str = "", **overrides) -> dict:
    sample = next(s for s in SAMPLES if s["key"] == key)
    body = {
        "items": copy.deepcopy(sample["items"]),
        "boxes": copy.deepcopy(sample["boxes"]),
        "instructions": instructions,
        "extraction": {"model": "gpt-4.1-nano"},
        "planning": {"model": "gpt-5.4-nano", "reasoning_effort": "low"},
    }
    body.update(overrides)
    return body


def sample_request(key: str = "A", instructions: str = "",
                   **overrides) -> RunRequest:
    return RunRequest.model_validate(sample_body(key, instructions,
                                                 **overrides))


def wait_terminal(client, run_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in TERMINAL:
            return run
        time.sleep(0.05)
    raise AssertionError("run did not finish")
