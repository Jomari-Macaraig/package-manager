"""Offline stand-in for the OpenAI client (tests and PLAYGROUND_FAKE_LLM=1).

Parse calls return, per `script` entry (consumed in order): a FlatResponse
(the plan), an Exception (raised), or None (a reply without a plan). With an
empty script the answer comes from `context` (the packer's plan, set by the
pipeline), by response_format: Guidance, StrategyAnswer, CheckAnswer (every
soft instruction honoured), NotesAnswer (template notes of the final plan) or
a FlatResponse. `create` (settings extraction) returns the default settings
JSON unless `create_script` has entries (str content, None, or an
Exception); with `echo_soft` (v2 runs) the default keeps the instruction text
as a soft instruction.
"""

import threading
from types import SimpleNamespace
from typing import Any

from packing.jobs.check import CheckAnswer, Verdict
from packing.jobs.notes import BoxNotes, NotesAnswer, template_notes
from packing.jobs.strategy import StrategyAnswer, strategy_answer_from_plan
from packing.planner import guidance_from_plan
from packing.schemas import FlatResponse, Guidance
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
        self.echo_soft = False  # v2: default extraction keeps the text
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
        elif self.echo_soft:
            text = kwargs["messages"][-1]["content"].split(
                "\n\nItems (id: name):\n")[0]
            content = PackingSettings(
                soft_instructions=[text]).model_dump_json()
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
        fmt = kwargs.get("response_format")
        if self.script:
            entry = self.script.pop(0)
        else:
            entry = self._default(fmt)
        if isinstance(entry, Exception):
            raise entry
        if fmt is Guidance and isinstance(entry, FlatResponse):
            entry = guidance_from_plan(entry)
        if fmt is StrategyAnswer and isinstance(entry, FlatResponse):
            entry = strategy_answer_from_plan(entry)
        if entry is None:
            message = SimpleNamespace(parsed=None, content=None,
                                      refusal="no plan")
        else:
            message = SimpleNamespace(
                parsed=entry, content=entry.model_dump_json(), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                               usage=_usage())

    def _default(self, fmt):
        """The unscripted answer for a response_format (None: no answer)."""
        if self.context is None:
            return None
        items, _boxes, settings, plan = self.context
        if fmt is CheckAnswer:
            return CheckAnswer(verdicts=[
                Verdict(index=n, verdict="honoured", reason="fake")
                for n in range(len(settings.soft_instructions))])
        if plan is None:
            return None
        if fmt is StrategyAnswer:
            return strategy_answer_from_plan(plan)
        if fmt is NotesAnswer:
            return NotesAnswer(boxes=[
                BoxNotes(**n) for n in template_notes(plan, items)])
        return plan
