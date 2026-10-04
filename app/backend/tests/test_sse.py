"""Drive event_stream directly (TestClient buffers streaming responses)."""

import asyncio
import json
import threading
import time
import uuid

import pytest

from app import store
from app.fake_llm import FakeClient
from app.runs import RunManager
from app.schemas import RunRequest
from app.sse import event_stream
from conftest import sample_body

META = {"request_id": str(uuid.uuid4()), "client_ip": "1.2.3.4",
        "user_agent": "pytest"}


async def never_disconnected() -> bool:
    return False


def frames_of(chunks: list[str]) -> list[dict]:
    out = []
    for chunk in chunks:
        if chunk.startswith(":"):
            continue
        fields = dict(line.split(": ", 1) for line in chunk.strip().split("\n"))
        out.append({"id": int(fields["id"]), "event": fields["event"],
                    "data": json.loads(fields["data"])})
    return out


async def collect(gen, until=None, limit=200) -> list[str]:
    chunks = []
    async for chunk in gen:
        chunks.append(chunk)
        if until and until(chunk):
            break
        assert len(chunks) < limit
    return chunks


@pytest.fixture
def manager():
    store.init()
    return RunManager()


def start(manager, **client_kwargs):
    manager.client_factory = lambda: FakeClient(**client_kwargs)
    return manager.start(RunRequest.model_validate(sample_body("A")), META)


def wait_slot_free(manager, timeout=10):
    deadline = time.monotonic() + timeout
    while manager.active_id() is not None:
        assert time.monotonic() < deadline
        time.sleep(0.02)


def test_live_stream_then_end(manager):
    gate = threading.Event()
    run_id = start(manager, gate=gate)

    async def go():
        gen = event_stream(manager, store, run_id, 0, never_disconnected,
                           poll_s=0.01, heartbeat_s=5)
        early = await collect(gen, until=lambda c: "event: packer" in c)
        assert not any("event: end" in c for c in early)
        gate.set()
        rest = await collect(gen)
        return early + rest

    frames = frames_of(asyncio.run(go()))
    assert [f["id"] for f in frames] == list(range(len(frames)))
    assert frames[0]["event"] == "started"
    assert frames[-1]["event"] == "end"
    assert frames[-1]["data"]["status"] == "succeeded"
    wait_slot_free(manager)


def test_live_resume_from_index(manager):
    gate = threading.Event()
    run_id = start(manager, gate=gate)
    gate.set()
    wait_slot_free(manager)

    async def go(start_index):
        return frames_of(await collect(event_stream(
            manager, store, run_id, start_index, never_disconnected,
            poll_s=0.01)))

    assert asyncio.run(go(3))[0]["id"] == 3
    # start index past the stored end: the stored end is still sent
    frames = asyncio.run(go(999))
    assert [f["event"] for f in frames] == ["end"]


def test_heartbeat_while_waiting(manager):
    gate = threading.Event()
    run_id = start(manager, gate=gate)

    async def go():
        gen = event_stream(manager, store, run_id, 0, never_disconnected,
                           poll_s=0.01, heartbeat_s=0.05)
        chunks = await collect(gen, until=lambda c: c == ": ping\n\n")
        gate.set()
        chunks += await collect(gen)
        return chunks

    chunks = asyncio.run(go())
    assert ": ping\n\n" in chunks
    assert "event: end" in chunks[-1]
    wait_slot_free(manager)


def test_stops_on_disconnect(manager):
    gate = threading.Event()
    run_id = start(manager, gate=gate)
    polls = {"n": 0}

    async def disconnected() -> bool:
        polls["n"] += 1
        return polls["n"] > 2

    async def go():
        return await collect(event_stream(
            manager, store, run_id, 0, disconnected, poll_s=0.01))

    chunks = asyncio.run(go())
    assert chunks and not any("event: end" in c for c in chunks)
    gate.set()
    wait_slot_free(manager)


class FlipStore:
    """Wraps store: the first events() read is stale (still running)."""

    def __init__(self, stale):
        self.stale = stale

    def events(self, run_id):
        if self.stale is not None:
            stale, self.stale = self.stale, None
            return stale
        return store.events(run_id)


def test_terminal_race_still_ends_with_end(manager):
    run_id = start(manager)
    wait_slot_free(manager)
    status, error, events = store.events(str(run_id))
    assert status == "succeeded"
    stale = ("running", None, events[:-1])  # read just before the finish
    wrapper = FlipStore(stale)

    async def go():
        return frames_of(await collect(event_stream(
            manager, wrapper, run_id, 0, never_disconnected, poll_s=0.01)))

    frames = asyncio.run(go())
    assert frames[-1]["event"] == "end"
    assert frames[-1]["data"]["status"] == "succeeded"
    assert [f["id"] for f in frames] == list(range(len(frames)))
    # and the row is terminal right after end
    assert store.get(str(run_id))["status"] == "succeeded"


def test_interrupted_run_end_sent_even_when_start_is_past_it(manager):
    rid = str(uuid.uuid4())
    store.create(run_id=rid, trace_id="t", request_id="r", client_ip="ip",
                 user_agent="ua", title="x", extraction_model="m",
                 planning_model="p", request_json="{}")
    store.mark_interrupted()  # NULL/empty events -> end with id 0

    async def go(start_index):
        return frames_of(await collect(event_stream(
            manager, store, rid, start_index, never_disconnected)))

    frames = asyncio.run(go(5))
    assert len(frames) == 1 and frames[0]["event"] == "end"
    assert frames[0]["data"]["status"] == "interrupted"
    assert frames[0]["id"] == 0


def test_running_row_without_active_slot_gets_synthetic_end(manager):
    rid = str(uuid.uuid4())
    store.create(run_id=rid, trace_id="t", request_id="r", client_ip="ip",
                 user_agent="ua", title="x", extraction_model="m",
                 planning_model="p", request_json="{}")

    async def go():
        return frames_of(await collect(event_stream(
            manager, store, rid, 0, never_disconnected, poll_s=0.05)))

    began = time.monotonic()
    frames = asyncio.run(go())
    assert time.monotonic() - began >= 1.9  # waited ~2 s for the row
    assert [f["event"] for f in frames] == ["end"]
    assert frames[0]["data"]["status"] == "failed"
    assert frames[0]["data"]["error"] == "Run state could not be saved"


def test_unknown_run_yields_nothing(manager):
    async def go():
        return await collect(event_stream(
            manager, store, uuid.uuid4(), 0, never_disconnected))

    assert asyncio.run(go()) == []
