"""Offline stand-in for the OpenAI client (tests and PLAYGROUND_FAKE_LLM=1).

Parse calls return, per `script` entry (consumed in order): a FlatResponse
(the plan), an Exception (raised), or None (a reply without a plan). With an
empty script the packer's own plan from `context` is returned, so no second
pack() call is needed. `create` (settings extraction) returns the default
settings JSON unless `create_script` has entries (str content, None, or an
Exception).
"""

import threading
from types import SimpleNamespace
from typing import Any

from packing.schemas import FlatResponse
from packing.settings import PackingSettings


def _usage() -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=1000, completion_tokens=200,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=0))


class FakeClient:
    def __init__(self, script: list | None = None,
                 create_script: list | None = None,
                 gate: threading.Event | None = None) -> None:
        self.script: list[FlatResponse | Exception | None] = list(script or [])
        self.create_script: list[str | Exception | None] = list(
            create_script or [])
        self.gate = gate  # parse() waits for it (tests of cancel/live SSE)
        self.context: tuple | None = None  # set by the pipeline
        self.create_calls: list[dict[str, Any]] = []
        self.parse_calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(
            create=self._create, parse=self._parse))

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.create_calls.append(kwargs)
        if self.create_script:
            content = self.create_script.pop(0)
            if isinstance(content, Exception):
                raise content
        else:
            content = PackingSettings().model_dump_json()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content=content, refusal=None, parsed=None))],
            usage=_usage())

    def _parse(self, **kwargs: Any) -> SimpleNamespace:
        # record a snapshot: the planner keeps appending to the same list
        self.parse_calls.append({**kwargs,
                                 "messages": list(kwargs["messages"])})
        if self.gate is not None:
            self.gate.wait(timeout=30)
        if self.script:
            entry = self.script.pop(0)
        else:
            entry = self.context[3] if self.context else None
        if isinstance(entry, Exception):
            raise entry
        if entry is None:
            message = SimpleNamespace(parsed=None, content=None,
                                      refusal="no plan")
        else:
            message = SimpleNamespace(
                parsed=entry, content=entry.model_dump_json(), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                               usage=_usage())
