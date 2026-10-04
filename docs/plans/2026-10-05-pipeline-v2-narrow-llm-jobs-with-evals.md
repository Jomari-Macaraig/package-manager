# Pipeline v2: Narrow LLM Jobs with Per-Job Evaluation — Implementation Plan

> **Revision 3.** Revision 2 resolved the first dry-run review's 9 blockers.
> Revision 3 resolves the second review's 3 blockers:
> - Sample G could not pass its test with Sample E's boxes
> - the test fixture changed on every run
> - the 409 text broke an existing frontend test
>
> It also folds in the second review's warnings: the adapter `outcome`,
> keeping jobs on a config error, choose rule 3, the "top" definition, the
> contract gaps, and the evaluation definitions.
>
> User decisions in this revision:
> - The box-pair packer search moves to its own separate plan.
> - Preferences are left/right/back/front and bottom/top (no "center").
> - Extract "correction" feedback is deferred.
> - The checker gets a per-verdict "wrong" toggle.
> - v1 stays the default page until Phase 4.
> - Fake-LLM runs are flagged and hidden on the Evaluation page.
> - Sample G uses Sample E's items with Wide + Medium boxes. A prototype in
>   the second review showed this is feasible.

## Overview

Build a second packing pipeline ("v2") next to the current one. The model does
four narrow jobs:

1. **Extract**: free text → `PackingSettings`
2. **Strategy**: pick box types, item order, rotations and simple placement
   preferences (code places every unit)
3. **Check**: judge whether the soft instructions are honoured
4. **Notes**: write loading steps for the person packing

Code does everything else.

Every job call is recorded with:
- code checks
- cost and latency
- model and prompt version

Each run takes 👍/👎 and a comment per job, and the checker has a per-verdict
"wrong" toggle. An Evaluation page aggregates all of this per job. This
evaluation data is the north star for improving each job.

Principles (from the user): start simple; keep the model's job narrow; always
have evaluation and metrics; have a clear pipeline, input and output.

## Current State Analysis

- v1 (`app/backend/app/pipeline.py:408` `run_pipeline`) runs extract → prepare
  → packer → plan → finalize. Its "improve" and "scratch" modes have the model
  write coordinates; on real runs that lost to the packer. "guide" mode (the
  model picks, code places) has never been tried with a real model.
- Pure code that v2 reuses as is (`app/backend/packing/`):
  - `packer.pack(items, boxes, settings, estimates)` and `packer.pack_guided`
  - `validation.validate_plan`, verified against an independent checker on
    7,525 plans
  - `metrics.plan_metrics` / `compare` / `tally`
  - `render_svg`
  - `settings.prepare_items` / `prepare_boxes` / `rules_text` / `final_plan`
  - `prompt_builder.build_user_prompt` with compact facts, and `estimates_table`
  - planner helpers: `_rank` (`planner.py:209`), `_coverage_errors` (`:216`),
    `_describe` (`:249`), `guidance_from_plan` (`:414`),
    `_guidance_feedback` (`:428`), `_CONFIG_ERRORS` (`:128`), `RunCancelled`
    (`:79`)
- Extraction (`packing/settings.py:182-232`) uses `chat.completions.create`
  with a hand-stripped strict JSON schema (`_SCHEMA`). It can't use `parse`:
  `PackingSettings` has defaults, and strict mode rejects defaults. Its bounds
  check is app-side and raises (`app/pipeline.py:163` `_check_settings`, which
  reads `config.SETTINGS_BOUNDS`).
- The `_check_ids(items, settings)` check at `settings.py:359` (raises on an unknown id) already validates every
  id-bearing settings field.
- The packer currently runs in the app layer (`app/pipeline.py:313`), which
  also sets `client.context = (items_p, boxes_p, settings, baseline)` there.
  `FakeClient._parse` (`app/fake_llm.py:62`) answers from `context[3]`.
- `RunManager` (`app/runs.py:47`) is hard-wired to v1's `store` and to
  `run_pipeline`.
  - `main.py:46` and `tests/test_sse.py:50` construct it with no arguments.
  - `active_id`, `get_active` and `cancel` return whatever is in the slot.
  - `_work` uses v1's `new_partial` / `partial_result` / `PipelineError`.
- `sse.event_stream(manager, store, …)` (`app/sse.py:47`) is already generic.
  It needs `store.events(rid) -> (status, error, events) | None`.
- CORS allows only GET and POST (`main.py:55`). The middleware lets a PUT with
  JSON through.
- Frontend:
  - `subscribe` (`api/client.ts:124-216`) calls v1's `getRun` for its polling
    and end refetch, and only knows v1 event types.
  - `useRunFlow` imports the v1 functions directly. `App.test.tsx` mocks
    `./api/client` at module level.
  - `PlanPanel` hard-codes v1 `renderUrl` / `svgDownloadUrl`, and its labels
    only know `model` and `packer`.
  - `PromptsPanel`, `HistoryList`, `PlansSideBySide` and `RunControls` /
    `formatEvent` are typed to v1.

## Desired End State

**Header and editor**
- The playground header has a `v1 | v2` switch and `Playground | Evaluation`
  tabs. The default is v1 during development; Phase 4 flips it to v2.
- The editor (samples, items, boxes, instructions) is shared by both
  pipelines.

**v2 settings.** Each job's default model is shown. An "Advanced" section
overrides model and effort per job for one run.

**v2 run page** shows, in order:
1. The final plan: an SVG per box with a step slider, cut-down heights, and
   the notes as numbered steps per box.
2. Which plan won (model or packer, or none) and why.
3. Both plans side by side, the metrics table, a preferences table (honoured
   per item) for the model plan *and* the packer plan, and the checker's
   verdicts.
4. A Jobs panel: one card per job with
   - status: ok / fallback / skipped
   - model, effort, prompt version
   - code checks ✓/✗ with detail
   - cost, latency, output
   - messages on demand
   - 👍/👎 and a comment. Check also gets a "wrong" toggle per verdict.
5. Run totals against the budget ($0.02, 30 s), with an over-budget badge.

**Evaluation page.** Per job, one row per prompt version + model + effort:
- calls, ok %, fallback %, all-checks-pass %, per-check pass %
- 👍/👎, average cost, p50/p95 latency, first and last seen
- the check job also shows its verdict-marked-wrong %
- the strategy job also shows the run-level "model won %"
- fake runs are hidden unless "Include fake runs" is ticked

**Failures.**
- Failed jobs degrade and never block the run.
- Exception: OpenAI configuration errors (auth, permission, not found, bad
  request) fail the run, because every job would fail.
- A run with no valid plan from either side still finishes as `succeeded`,
  with `final: null`, and the run page says so.

**Run slot.** One run at a time across v1 and v2. Each pipeline's "active
run" endpoint only reports its own runs.

**Prompt versions are enforced.** A test fails if a job's prompt, answer
schema or user-message template changes without a version bump.

**Samples.** v2 serves samples A–E plus three instruction samples, F–H.

**v1 is unchanged** apart from refactors that keep its behaviour and tests:
- the generic `RunManager` with v1 defaults
- options on `subscribe`, a generic `useRunFlow`, and props on `PlanPanel`
- the 409 body gains a `kind` field (`"v1"` / `"v2"`); its `detail` text is
  unchanged

### Key Discoveries:
- `_fill_box` (`packer.py:104`) tries candidate points in `sorted(points)`
  order, i.e. (x, y, z). The extreme points are only the three corners of
  each placed unit (`:113`). Preferences need their own point order and, for
  side preferences, extra candidate points against the wall.
- `pack_guided` returns `(None, errors)` for bad choices and
  `(flat, validate_plan errors)` otherwise. That lets the `choices_valid` and
  `plan_valid` checks be told apart.
- `LengthFinishReasonError` and `ContentFilterFinishReasonError` are
  `OpenAIError`s, not `APIError`s. Copy `planner._ask`'s (`planner.py:156-195`)
  handling exactly.
- `planner` imports `packer`, so `packer` must not import `planner`.
  `plan_score` lives in `validation`.
- Coordinates: z runs left to right and y back to front (`schemas.py:183`),
  so left = small z, back = small y.

## Pipeline v2 — Input, Stages, Output

### Input (`POST /api/v2/runs`)
```json
{
  "items": [{"name": "Kettle", "color": "silver", "weight": 2.2, "x": 8, "y": 7, "z": 7, "quantity": 5}],
  "boxes": [{"name": "Medium", "weightMax": 50, "x": 10, "y": 12, "z": 16, "stock": null}],
  "instructions": "Keep the jars on the left and the kettles on the right.",
  "jobs": {
    "extract":  {"model": "gpt-4.1-nano", "reasoning_effort": null},
    "strategy": {"model": "gpt-5.4-nano", "reasoning_effort": "low"},
    "check":    {"model": "gpt-4.1-mini", "reasoning_effort": null},
    "notes":    {"model": "gpt-4.1-nano", "reasoning_effort": null}
  }
}
```
- `items`, `boxes` and `instructions` are validated by a shared base model,
  `OrderInput`, extracted from v1's `RunRequest._caps` / `_instructions`
  (`app/schemas.py:108-130`). v1's `RunRequest` then extends it.
- Caps: 20 item rows, 10 box rows, 150 units, 2,000 instruction characters.
  Units are inches and pounds.
- `jobs` is optional, and so is each entry. Unknown keys are rejected (422),
  through `extra="forbid"` on the v2 request models.
- The model must be on that job's allowed list. The effort must be one the
  model supports; `null` for non-reasoning models.

### Stages
| # | Stage | Who | In → Out | Fallback |
|---|---|---|---|---|
| 1 | Extract | model | instructions + item list → `PackingSettings` | `PackingSettings(soft_instructions=[text])` |
| 2 | Prepare | code | settings → prepared items/boxes, rules text, compact facts | — |
| 3 | Packer | code | → baseline plan + `box_estimates` (**today's packer**) | — |
| 4 | Strategy | model | compact facts → boxes, order, rotations, preferences, reason (≤ 2 calls) | the packer plan |
| 5 | Place + validate | code | choices → plan (`pack_guided` with preferences) → `validate_plan`, coverage; `preference_report` on the model plan **and** the packer plan | — |
| 6 | Check | model | soft instructions + model plan + code preference facts → one verdict per instruction | every verdict `cannot_tell` |
| 7 | Choose | code | model plan vs packer plan → final plan + reason | — |
| 8 | Notes | model | final plan + soft instructions → 1–8 steps per box | code template notes |
| 9 | Finalize | code | cut-down notes, SVGs, metrics, totals vs budget | — |

Check (stage 6) is skipped when there are no soft instructions or the model
has no valid plan. Notes (stage 8) are skipped when there's no final plan.

**Choose rule (stage 7)** — `packing/v2.choose`. The first rule that matches
wins:
1. **Neither side has a valid plan** → `source "none"`, `final null`.
   Reason: `"no valid plan"`.
2. **Only one side has a valid plan** → that side wins.
   Reason: `"only valid plan"`.
3. **The model wins on the instructions** when all of these hold
   (everything here can be computed in code):
   - there are soft instructions
   - every verdict is `honoured`
   - the model plan has at least one preference row, and every row has
     `honoured == total`
   - the packer plan does **not** honour those same preferences: evaluated on
     the packer plan, at least one row has `honoured < total`
   - the model plan has no more leftover units than the packer's
   - it uses no more boxes
   - its shipped volume is ≤ `VOLUME_MARGIN (1.15)` × the packer's

   Reason: `"honours the instructions"`.
4. **Otherwise** `_rank` decides, and a tie goes to the model.
   Reasons:
   - `"same as packer"` when the ranks are equal and nothing is packed
   - `"better metrics"`
   - `"packer has better metrics"`

### Output — contract (authoritative; the frontend and backend share it)

**Pydantic (`app/backend/app/schemas_v2.py`), with the TypeScript equivalents in
`app/frontend/src/api/typesV2.ts`:**
```python
JobName = Literal["extract", "strategy", "check", "notes"]
JobStatus = Literal["ok", "fallback", "skipped"]
Side = Literal["left", "right", "back", "front", "any"]
Level = Literal["bottom", "top", "any"]

class JobChoice(BaseModel):            # extra="forbid"
    model: str
    reasoning_effort: Literal["low", "medium", "high"] | None = None

class RunRequestV2(OrderInput):        # extra="forbid"
    jobs: dict[JobName, JobChoice] = {}

class CheckResult(BaseModel):
    name: str; passed: bool; detail: str

class Tokens(BaseModel):
    prompt: int; completion: int; reasoning: int

class Feedback(BaseModel):
    rating: Literal["up", "down"] | None   # None = cleared / comment only
    comment: str                       # "" when none
    wrong_verdicts: list[int]          # check job only; [] otherwise
    updated_at: str

class JobView(BaseModel):              # RunV2.jobs: 4 rows, in pipeline order, once terminal
    job: JobName
    status: JobStatus
    model: str | None                  # None when skipped before a model was chosen
    reasoning_effort: str | None
    prompt_version: str
    calls: int                         # 0 when skipped
    checks: list[CheckResult]          # [] when skipped
    output: dict | list | None         # what the pipeline used (answer or fallback)
    answer: dict | list | None         # parsed model answer, None if none
    error: str | None                  # e.g. "cancelled", "not run", "LengthFinishReasonError: …"
    cost: float                        # 0 when skipped
    latency_s: float
    tokens: Tokens
    feedback: Feedback | None          # None until feedback is given

class PlanSideV2(BaseModel):           # same fields for both sides
    ok: bool
    plan: Plan | None                  # nested Response (v1 type)
    box_count: int
    rank: list[float] | None
    errors: list[str]
    hints: list[str]                   # always [] in v2

class PreferenceRow(BaseModel):
    id: int; name: str; side: Side; level: Level
    honoured: int; total: int; detail: str

class Verdict(BaseModel):
    index: int; instruction: str
    verdict: Literal["honoured", "not_honoured", "cannot_tell"]; reason: str

class BoxNotes(BaseModel):
    box: int; steps: list[str]

class FinalV2(BaseModel):
    source: Literal["model", "packer"]
    reason: str
    plan: Plan                         # plan.notes = cut notes only
    notes: list[BoxNotes]
    cut_notes: list[str]

class Totals(BaseModel):
    cost: float; seconds: float; over_budget: bool; budget: dict  # {"usd", "seconds"}

class RunV2(BaseModel):
    run_id: str; status: RunStatus; error: str | None
    created_at: str; finished_at: str | None
    fake: bool
    request: RunRequestV2
    events: list[RunEventV2]
    settings: PackingSettings | None
    prepared_items: list[PreparedItem] | None
    prepared_boxes: list[PreparedBox] | None
    jobs: list[JobView]                # 4 rows once terminal; [] while running, and [] for the
                                       # rare terminal run whose finish fell back to set_status
                                       # (UI shows "No job data")
    packer: PlanSideV2 | None
    model: PlanSideV2 | None
    source: Literal["model", "packer", "none"] | None   # authoritative winner; None while running
    reason: str | None                 # choose reason, also when source == "none"
    final: FinalV2 | None
    preferences_model: list[PreferenceRow]   # [] when none
    preferences_packer: list[PreferenceRow]
    verdicts: list[Verdict]            # [] when check was skipped
    comparison: list[MetricRow] | None
    totals: Totals | None

class RunSummaryV2(BaseModel):
    run_id: str; created_at: str; finished_at: str | None; status: RunStatus
    title: str; source: Literal["model", "packer", "none"] | None; reason: str | None
    total_cost: float | None; total_s: float | None; over_budget: bool; fake: bool

# events — every event carries id: int and t: float (seconds since start)
StartedV2  = {"type": "started"}
JobEventV2 = {"type": "job", "job": JobName, "status": JobStatus, "passed": int, "total": int}
PackerV2   = {"type": "packer", "ok": bool, "boxes": int}
ChosenV2   = {"type": "chosen", "source": "model"|"packer"|"none", "reason": str}
EndV2      = {"type": "end", "status": RunStatus, "error": str | None}

class FeedbackIn(BaseModel):           # extra="forbid"
    rating: Literal["up", "down"] | None = None
    comment: str = Field("", max_length=2000)
    wrong_verdicts: list[int] = []     # only allowed for job "check"; indices must exist

class EvalGroup(BaseModel):
    job: JobName; prompt_version: str; model: str | None; reasoning_effort: str | None
    calls: int                         # non-skipped calls
    skipped: int                       # count of skipped rows
    ok_rate: float | None              # status ok / calls; None when calls == 0
    fallback_rate: float | None
    all_checks_rate: float | None      # share of calls where every check passed
    per_check: dict[str, float]        # check name -> pass share over calls that ran it
    thumbs_up: int; thumbs_down: int
    wrong_verdict_rate: float | None   # check only: wrong verdicts / verdicts on rated runs
    model_won_rate: float | None       # strategy only, run-level: share of succeeded runs with source == "model"
    avg_cost: float | None; p50_latency_s: float | None; p95_latency_s: float | None  # nearest-rank
    first_seen: str; last_seen: str

class ModelOptionV2(BaseModel):
    id: str; label: str
    efforts: list[Literal["low", "medium", "high"]] | None   # None for non-reasoning / fixed-kwargs models
    price_in: float; price_out: float

class JobConfigView(BaseModel):
    default: JobChoice; models: list[ModelOptionV2]

class ConfigV2(BaseModel):
    jobs: dict[JobName, JobConfigView]
    budget: dict                       # {"usd": float, "seconds": float}
    caps: Caps                         # v1 type
    volume_margin: float

class SampleV2(Sample):                # key: Literal["A".."H"]
    ...

RunEventV2 = StartedV2 | JobEventV2 | PackerV2 | ChosenV2 | EndV2   # discriminated on "type"
```
- These are **reused from v1** (`app/schemas.py`, `src/api/types.ts`):
  `Plan`, `RunStatus` (includes `interrupted`), `MetricRow`, `PreparedItem`,
  `PreparedBox`, `PackingSettings`, `Caps`, `Sample`.
- In TypeScript, `output` and `answer` are `unknown`, and `budget` is
  `{usd: number; seconds: number}`. Everything else maps 1:1.
- A shared fixture, `app/backend/tests/fixtures/run_v2.json`, is written by a
  backend test **only when `UPDATE_FIXTURES=1`**. It comes from a fake run of
  Sample G, with the run id, timestamps, event `t` values and latencies
  replaced by fixed values (`00000000-0000-4000-8000-000000000001`,
  `2026-01-01T00:00:00.000Z`, `t = id`, `latency_s = 1.0`), so the file is
  deterministic. Without the flag, the test only validates the committed file
  against `RunV2`.
- The frontend's `src/test/fixturesV2.ts` imports a copy of that fixture.
  A backend test fails when the copy is out of date: it compares the two
  files' JSON.

## What We're NOT Doing

- **The packer box-pair search.** It goes in a separate plan, gated by the
  Sample E 3,584 in³ test. v2 uses today's packer.
- Porting v1's improve/scratch modes, `repair.py` help, starting plan or
  back-to-best into v2.
- Changing v1's behaviour. The only v1 touches are refactors with v1 defaults:
  - `RunManager`
  - `subscribe` options, `useRunFlow`, `PlanPanel` props
  - the 409 body's new `kind` field
- The extract "correction" field, and the "center" side preference.
- Notebook (`labs/`) changes.
- A golden-set runner, an offline eval CLI, CI evals against real models, or
  exporting feedback.
- An LLM judge used for *evaluation*. The checker is a pipeline step; it is
  evaluated by human feedback and code checks.
- Moving to the Agents SDK, tool calling or the Responses API.
- Auth, multiple users, concurrent runs, data retention or cleanup.
- Retrying jobs, except the strategy job's single second call.

## Implementation Approach

1. All v2 logic lives in pure modules under `app/backend/packing/`:
   - `jobs/`: one module per job, plus `base.py`
   - `v2.py`: the orchestrator

   They import no FastAPI, `app` or `agents`. The app-only inputs (bounds,
   token limits, prices) are passed in.
2. The app layer adds:
   - storage (`store_v2.py`)
   - contract models (`schemas_v2.py`)
   - an adapter that calls `run_v2` and computes costs, SVGs and metrics
     (`pipeline_v2.py`)
   - routes (`routes_v2.py`)
   - a generic `RunManager` whose v1 defaults keep today's behaviour
3. The frontend adds:
   - a v2 API client and types
   - options on `subscribe`
   - two `useRunFlow` instances
   - URL props on `PlanPanel`
   - v2 components and the Evaluation page
4. Each job module owns its prompt, version, schema, user-message builder,
   code checks and fallback. That keeps a job's evaluation next to the job.

## Agent Teams

### Implementation Agents

#### Backend Engineer (Wave 1)
- **Agent type**: `voltagent-lang:python-pro`
- **Branch**: `plan/pipeline-v2-backend`
- **Phases**: 1, 2
- **Directories**: `app/backend/packing/`, `app/backend/app/`, `app/backend/tests/`
- **Dependencies**: none
- **Context files**:
  - this plan
  - `app/backend/packing/CLAUDE.md`, `app/CLAUDE.md`
  - `app/backend/packing/{packer,planner,settings,prompt_builder,metrics,validation}.py`
  - `app/backend/app/{runs,store,routes,sse,pipeline,config,fake_llm,schemas,samples,main,middleware}.py`
  - `app/backend/tests/{conftest,contract,test_sse,test_api}.py`
- **Prompt**: Implement Phases 1 and 2 exactly as specified, including the
  Output contract (`schemas_v2.py`) and the shared fixture JSON. Keep v1's
  behaviour, and every v1 test green and unchanged.
- **Acceptance criteria**:
  - [ ] `cd app/backend && uv run pytest` passes (v1 + v2)
  - [ ] Fake run for samples A–H via `/api/v2/runs`: each ends `succeeded`, with 4 `job_calls` rows and `fake = 1`
  - [ ] Samples F–H constraint tests pass (see Phase 2 §5)
  - [ ] `tests/test_prompt_versions.py` passes
  - [ ] `tests/fixtures/run_v2.json` is committed (generated with `UPDATE_FIXTURES=1`, deterministic), and validates against `RunV2`

#### Frontend Engineer (Wave 1, parallel)
- **Agent type**: `voltagent-lang:react-specialist`
- **Branch**: `plan/pipeline-v2-frontend`
- **Phases**: 3
- **Directories**: `app/frontend/src/`
- **Dependencies**: none
  - builds against the Output contract
  - uses a hand-written `fixturesV2.ts` that matches it; Integration later
    replaces it with the backend fixture
- **Context files**:
  - this plan
  - `app/CLAUDE.md`
  - `app/frontend/src/{App.tsx,App.test.tsx,api/client.ts,api/client.test.ts,api/types.ts,hooks/useRunFlow.ts,lib/editor.ts,lib/events.ts,test/setup.ts,test/fixtures.ts}`
  - `app/frontend/src/components/{RunView,PlanPanel,PlansSideBySide,MetricsTable,HistoryList,SettingsPanel,RunControls,SamplePicker,ModelSettings}.tsx`
- **Prompt**: Implement Phase 3. v1 stays the default pipeline, and every
  existing test passes unchanged.
- **Acceptance criteria**:
  - [ ] `npm run typecheck` and `npm test` pass; existing tests unchanged
  - [ ] All new controls have labels; toggles use `aria-pressed`; tabs use `role="tab"` / `aria-selected` / `aria-controls`
  - [ ] `RunViewV2` renders `fixturesV2` with every section; feedback calls `PUT /api/v2/runs/{id}/feedback/{job}`

#### Integration Engineer (Wave 2)
- **Agent type**: `voltagent-core-dev:fullstack-developer`
- **Branch**: `plan/pipeline-v2-integration`
- **Phases**: 4
- **Directories**: `app/`, `CLAUDE.md`, `app/CLAUDE.md`, `app/backend/packing/CLAUDE.md`
- **Dependencies**: Backend and Frontend merged
- **Context files**: this plan, both merged branches
- **Prompt**: Merge both branches, then:
  - replace `fixturesV2.ts` with the backend fixture copy
  - run the smoke script and the dev servers with the fake LLM
  - fix contract mismatches
  - flip the default pipeline to v2 and update the App tests that need it
  - write the docs
- **Acceptance criteria**:
  - [ ] `scripts/smoke_v2.py` exits 0
  - [ ] Both test suites pass, and the fixture-sync test passes
  - [ ] The default pipeline is v2; the v1 App tests select v1 explicitly
  - [ ] Docs updated per the global CLAUDE.md rule

#### Test Engineer (Wave 3)
- **Agent type**: `voltagent-qa-sec:test-automator`
- **Branch**: `plan/pipeline-v2-tests`
- **Phases**: Testing Strategy
- **Directories**: `app/backend/tests/`, `app/frontend/src/**/*.test.tsx`
- **Dependencies**: Integration merged
- **Context files**: this plan, the integrated code
- **Prompt**: Fill the gaps in "Testing Strategy", especially:
  - every fallback path
  - every code check, passing and failing
  - every choose rule
  - feedback validation
  - the eval maths, including null rates and nearest-rank percentiles
  - the Jobs panel and Evaluation page states
- **Acceptance criteria**:
  - [ ] Each Phase 1 check has a passing test and a failing test
  - [ ] Each degradation path and each choose rule has a test
  - [ ] Both suites pass

### Execution Order

| Wave | Agents | Mode | Depends On |
|------|--------|------|------------|
| 1 | Backend Engineer, Frontend Engineer | own branches (parallel) | — |
| 2 | Integration Engineer | own branch | Wave 1 merged |
| 3 | Test Engineer | own branch | Wave 2 merged |
| 4 | Bug Reviewer, Code Quality Reviewer, Accessibility Reviewer | read-only (parallel) | Wave 3 merged |
| 5 | Meta-Reviewer (Pragmatism Filter) | read-only | Wave 4 complete |
| 6 | Fix agents (only if there are blockers) | own branch | Wave 5 verdict |

### Review Agents

#### Bug Reviewer
- **Agent type**: `voltagent-qa-sec:code-reviewer`
- **Scope**: `packing/jobs/`, `packing/v2.py`, `packing/packer.py` (preferences), `app/{runs,store_v2,schemas_v2,pipeline_v2,routes_v2,fake_llm,main}.py`, the frontend v2 files and the generalised `subscribe` / `useRunFlow` / `PlanPanel`
- **Focus areas**: degradation paths, the choose rule, the shared slot and its `kind` filtering, finish idempotency, feedback validation, eval maths, v2 SSE
- **Checklist**:
  - [ ] Every job failure records `status="fallback"` plus an error, and the run still finishes. Jobs that never ran are recorded as `skipped` with `"not run"` or `"cancelled"`.
  - [ ] OpenAI config errors fail the run; cut-offs, content filter, schema and API errors degrade. Usage is kept on failures.
  - [ ] v1 `/api/runs/active` never reports a v2 run, and the reverse. A v1 run makes v2 return 409, and the reverse. The slot is released on every path.
  - [ ] `job_calls` is inserted only when the guarded finish updated the `runs_v2` row; retries are idempotent (`ON CONFLICT DO NOTHING`).
  - [ ] Choose rules 1–4 are implemented exactly, including "the packer doesn't honour" and the 1.15 margin.
  - [ ] Eval rates exclude skipped rows, are `None` when there are 0 calls, and exclude fake runs by default.
  - [ ] Preferences never produce a plan that breaks a rule (it is validated).
- **Severity guide**: blocker = a wrong winner, a lost or duplicated evaluation row, a v1 regression, a stuck slot, a crash on a sample; warning = a wrong Evaluation number; suggestion = everything else

#### Code Quality Reviewer
- **Agent type**: `voltagent-qa-sec:code-reviewer`
- **Scope**: all new and refactored backend and frontend files
- **Focus areas**: existing style, the `packing` → app boundary, job self-containment, no duplication where props or options suffice
- **Checklist**:
  - [ ] `packing/` imports no FastAPI, `app` or `agents`; bounds, limits and prices are passed in
  - [ ] Each job module: `PROMPT_VERSION`, `USER_TEMPLATE_VERSION`, `SYSTEM_PROMPT`, the answer schema, `build_messages`, `run_*`, `checks`, the fallback
  - [ ] v1 call sites unchanged (default arguments); v1 tests unchanged (the 409 `detail` text is kept; only a `kind` field is added)
  - [ ] No copy-paste of v1 components where props suffice
- **Severity guide**: blocker = a `packing` → app import, a broken v1 contract; warning = duplication; suggestion = naming

#### Accessibility Reviewer
- **Agent type**: `voltagent-qa-sec:accessibility-tester`
- **Scope**: the switch, tabs, `JobSettings`, `RunViewV2`, `JobsPanel` / `JobCard`, `FeedbackControls`, `VerdictsList`, `EvaluationPage`
- **Checklist**:
  - [ ] The switch uses `aria-pressed` buttons; tabs use `role=tablist/tab/tabpanel` with `aria-selected` / `aria-controls`; the editor stays mounted (hidden) when the Evaluation tab is shown
  - [ ] The 👍/👎 emoji are `aria-hidden`, and their accessible names are "Good" / "Bad"; the per-verdict toggle is labelled with the instruction
  - [ ] Statuses and checks are in text (✓/✗ plus a word), not colour only
  - [ ] Evaluation tables have a caption and `th scope`; loading and error states are announced (`role="status"` / `alert`)
- **Severity guide**: blocker = a control that can't be reached or named; warning = colour-only status; suggestion = polish

### Meta-Reviewer (Pragmatism Filter)
- **Agent type**: `voltagent-qa-sec:architect-reviewer`
- **Input**: all review agent findings
- **Filters**:
  - KEEP as blocker: production bugs, real security vulnerabilities, broken functionality (including v1), violated project conventions
  - DOWNGRADE to suggestion: best practices the codebase doesn't follow, future-proofing, style preferences
  - REJECT: scope creep, anything contradicting "What We're NOT Doing", rewrites of working code outside this plan
- **Output**: a Final Review Verdict with a GO/NO-GO decision

---

## Phase 1: Packing core (pure code, plus FakeClient)

### Overview
Placement preferences, the four jobs with checks and fallbacks, the v2
orchestrator, and FakeClient support. `app/fake_llm.py` is the only app file
this phase touches.

### Changes Required:

#### 1. Placement preferences
**File**: `app/backend/packing/packer.py`
**Changes**:

- **New argument:**
  `pack_guided(..., preferences: Mapping[int, Mapping[str, str]] | None = None)`.
  Each entry is `{"side": "any|left|right|back|front", "level": "any|bottom|top"}`.
- **Order (level):** stable. `bottom` item types go first, then `any`, then
  `top`. Within each group the model's order is kept.
- **New hook on `_fill_box`:** an optional per-item
  `point_order(pos, dims, size) -> tuple`, plus optional `extra_points(dims, size, points) -> set`.
  When both are `None`, behaviour is exactly today's (`sorted(points)`, no
  extra points), so v1 placements are unchanged (a test asserts this).
- **Side preferences:**
  - Sort keys, height first: `left (x, z, y)`; `right (x, -(z + dz), y)`;
    `back (x, y, z)`; `front (x, -(y + dy), z)`.
  - Wall-anchored candidate points: for `right`, each existing point `(x, y, z)`
    also adds `(x, y, Z - dz)`; for `front`, `(x, Y - dy, z)`. For `left` and
    `back`, add `(x, y, 0)` and `(x, 0, z)`.
  - All candidates are still checked by `_fits_here`.
- **Level `top`:** sort key `(-x, y, z)`, so the highest valid spot comes
  first.
- **Level `bottom`:** order only, using the default key.
- **New function:** `preference_report(flat, items, boxes, preferences) -> list[dict]`
  returns one row per item with a preference: `{id, name, side, level, honoured, total, detail}`.
  A unit honours its preference when *every* stated part holds, in box
  coordinates:
  - left: unit centre `cz < Z/2`; right: `cz > Z/2`
  - back: `cy < Y/2`; front: `cy > Y/2`
  - bottom: `x == 0`
  - top: no unit of an item *without* a `top` preference sits above it (its
    Y–Z footprint overlaps and its bottom is at or above this unit's top). So
    several top units may stack on each other.

  `detail` reads, for example, `"3/3 on the left half"` or
  `"1/2 on top"`.

  The report uses the **prepared** box and item sizes, i.e. after
  wall clearance and padding.

  A `bottom` preference can conflict with no-stack or floor-only rules on
  other items. The report then shows `honoured < total`, which is correct:
  it measures the outcome, not blame.

#### 2. Jobs package
**Files**: `app/backend/packing/jobs/__init__.py`, `base.py`, `extract.py`, `strategy.py`, `check.py`, `notes.py`
**Changes**:

`base.py`:
```python
@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

@dataclass
class JobConfig:
    model: str
    reasoning_effort: str | None = None      # sent as reasoning_effort when not None
    max_completion_tokens: int = 2000
    extra_kwargs: dict = field(default_factory=dict)  # e.g. {"reasoning_effort": "none"} for gpt-5.4-nano on extract/check/notes

@dataclass
class JobResult:
    job: str                 # "extract" | "strategy" | "check" | "notes"
    status: str              # "ok" | "fallback" | "skipped"
    output: Any              # what the pipeline uses (answer or fallback)
    answer: Any = None       # parsed model answer, if any
    checks: list[Check] = field(default_factory=list)
    model: str | None = None
    reasoning_effort: str | None = None
    prompt_version: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    latency_s: float = 0.0
    error: str | None = None
    messages: list[dict] = field(default_factory=list)

def call_parsed(client, cfg, messages, schema) -> CallOutcome
    """chat.completions.parse(response_format=<pydantic model>)."""
def call_json(client, cfg, messages, json_schema: dict, validate) -> CallOutcome
    """chat.completions.create(response_format={"type": "json_schema", ...}),
    then validate(content). Used by extract (PackingSettings has defaults, so
    strict parse is not possible)."""
```
- `CallOutcome` is `(parsed | None, error | None, usage counts, latency_s)`.
- Both helpers copy `planner._ask` (`planner.py:156-195`) exactly:
  - `planner._CONFIG_ERRORS` are re-raised (they fail the run)
  - `LengthFinishReasonError` / `ContentFilterFinishReasonError` count
    `exc.completion.usage` and return an error
  - `ValidationError` returns an error (with usage when the response is
    available)
  - other `openai.APIError`s return an error
  - a refusal or an empty parse returns an error
- Neither helper retries.

The four jobs. Each module has `PROMPT_VERSION`, `USER_TEMPLATE_VERSION`,
`SYSTEM_PROMPT`, an answer schema, `build_messages(...)`, `run_<job>(...) -> JobResult`
and its checks:

| Job | Skipped when | Answer schema | Code checks (name: passes when …) | Fallback output |
|---|---|---|---|---|
| `extract` (`extract-v1`) | instructions empty | `PackingSettings` via `call_json` + `settings._SCHEMA`; `SYSTEM_PROMPT = settings.EXTRACT_PROMPT` (the prompt stays in `settings.py`) | `parsed` · `known_ids`: `settings._check_ids(items, settings)` does not raise · `in_bounds`: `settings_problems(settings, bounds) == []` (new, non-raising, in `settings.py`; includes the duplicate block rule check; `bounds` is passed in; the app's `_check_settings` becomes a wrapper raising on its result) · `applies`: `prepare_items` / `prepare_boxes` don't raise · `not_empty`: at least one non-default field or soft instruction | `PackingSettings(soft_instructions=[text.strip()])`, status `fallback`, used whenever `parsed` / `known_ids` / `in_bounds` / `applies` / `not_empty` fails |
| `strategy` (`strategy-v1`) | never | `StrategyAnswer{boxes: list[int], order: list[int], rotations: list[RotationChoice], preferences: list[Preference{id, side, level}], reason: str}` | `parsed` · `choices_valid` · `plan_valid` · `no_extra_leftovers` (`_coverage_errors` empty) · `no_extra_boxes` (≤ the packer's) · `volume_within_margin` (≤ 1.15 × the packer's shipped volume) · `preferences_honoured` (every report row `honoured == total`; passes with detail "none" when there are no preferences) | the packer plan (status `fallback`) when there's no valid plan after ≤ 2 calls |
| `check` (`check-v1`) | no soft instructions, or no valid model plan | `CheckAnswer{verdicts: list[Verdict{index: int, verdict: "honoured"\|"not_honoured"\|"cannot_tell", reason: str}]}` | `parsed` · `covers_all`: each instruction index appears exactly once | every verdict `cannot_tell` |
| `notes` (`notes-v1`) | no final plan | `NotesAnswer{boxes: list[BoxNotes{box: int, steps: list[str]}]}` | `parsed` · `one_entry_per_box` · `step_count` (1–8 per box) · `mentions_items` · `counts_match` | `template_notes(plan)` (code, per box: "Load N × Name" in seq order) |

Strategy details:
- **Prompt:** an adaptation of `GUIDE_SYSTEM_PROMPT`. It adds the preference
  vocabulary and its meaning in box terms, drops notes, and asks for a
  one-sentence reason.
- **User message:** `build_user_prompt` (compact facts) + `box_estimates`.
- **Calls:** a second call happens only when the first doesn't parse, or gives
  invalid choices, an invalid plan, or extra leftovers. It sends
  `planner._guidance_feedback`.
- **Max tokens:** taken from the effort (passed in).

Check details:
- **User message:**
  - the soft instructions, numbered from 0
  - a plan table: box, name, seq, item, pos, dims, `floor` / `on seq N`,
    `on top` (no non-top unit above it)
  - "Code facts", one `preference_report` line per row
- **Prompt:** trust the code facts; answer `cannot_tell` rather than guess.

Notes details:
- **User message:** the box list (number, name), units in seq order (item
  name, `floor` / `on seq N`), and the soft instructions.
- **Prompt:** 1–8 short imperative steps per box, no cut-down heights, and
  only the listed item names.
- **Name normalisation** for `mentions_items` / `counts_match`:
  lower-case, drop parentheses and their contents, collapse spaces, accept a
  plural "s". "Tape block of 4 (1×1×4)" becomes "tape block of 4".
  - `mentions_items` passes when every normalised item name of the box appears
    in that box's steps.
  - `counts_match` only checks the patterns `"<n> × <name>"`, `"<n> x <name>"`
    and `"<n> <name>"`; the number must equal that item's unit count in the box.

`tests/test_prompt_versions.py` pins one hash per job:
`PINNED[(job, PROMPT_VERSION, USER_TEMPLATE_VERSION)] = sha256(...)` over
three parts, concatenated:
- `SYSTEM_PROMPT`
- `json.dumps(answer_schema_json, sort_keys=True)`
- the user message that `build_messages` renders for a fixed input (Sample G
  prepared, with fixed estimates, plan and instructions)

Changing a prompt, a schema or a user-message template without bumping the
matching version fails the test.

#### 3. Orchestrator
**File**: `app/backend/packing/v2.py`
**Changes**:
```python
VOLUME_MARGIN = 1.15

class RunCancelledV2(RunCancelled):    # carries partial job results
    def __init__(self, jobs: list[JobResult]): ...

@dataclass
class V2Result:
    settings: PackingSettings
    items_p: list[dict]
    boxes_p: list[dict]
    estimates: list[dict]
    packer_flat: FlatResponse | None
    packer_errors: list[str]
    model_flat: FlatResponse | None
    model_errors: list[str]
    preferences_model: list[dict]
    preferences_packer: list[dict]
    verdicts: list[dict]
    source: str              # "model" | "packer" | "none"
    reason: str
    final_flat: FlatResponse | None
    notes: list[dict]        # [{box, steps}]
    jobs: list[JobResult]    # always 4, in pipeline order

def run_v2(client, items, boxes, instructions, jobs: dict[str, JobConfig], *,
           bounds: dict, on_event=None, should_stop=None,
           on_context=None) -> V2Result
def choose(model_flat, packer_flat, verdicts, soft_instructions,
           prefs_model, prefs_packer, boxes) -> tuple[str, str]   # (source, reason)
```
- **`on_event` receives:**
  - `{"type": "job", "job", "status", "passed", "total"}` after each job
  - `{"type": "packer", "ok", "boxes"}`
  - `{"type": "chosen", "source", "reason"}`
- **`on_context(stage, data)` is called:**
  - after the packer: `("packer", (items_p, boxes_p, settings, packer_flat))`
  - before notes: `("final", (items_p, boxes_p, settings, final_flat))`

  The app adapter forwards it to `client.context` when the client has a
  `context` attribute. Real clients ignore it.
- **`should_stop`** is checked before each stage. It raises `RunCancelledV2`
  with all 4 jobs: jobs that didn't run are `skipped` with
  `error="cancelled"`.
- **Config errors** (re-raised from a job) are wrapped in
  `ConfigErrorV2(original, jobs)`. `jobs` holds the finished jobs plus the
  remaining ones as `skipped` with `error="not run"`, which keeps them through
  the error. The adapter turns it into a failed run (via v1's
  `_openai_error`) and stores those jobs.

#### 4. FakeClient
**File**: `app/backend/app/fake_llm.py`
**Changes**: by default, by `response_format` (checked before the existing
`Guidance` branch):
- `StrategyAnswer`: from `context[3]` (the packer plan) — `guidance_from_plan`
  plus `preferences=[]` and `reason="fake"`, through a new
  `strategy_answer_from_plan`
- `CheckAnswer`: one `honoured` verdict per `context[2].soft_instructions`
- `NotesAnswer`: `template_notes(context[3])`, where `context[3]` is the
  final plan after the `"final"` `on_context`
- `PackingSettings`: via `create`.
  - v1 default is unchanged: `PackingSettings()`.
  - New flag `FakeClient.echo_soft`. When the v2 adapter sets it to `True`,
    the default instead returns
    `PackingSettings(soft_instructions=[<the instruction text from the user message>])`.
    That way samples F–H exercise the check job in fake runs.

Scripted entries still override all of these. The fake strategy returns no
preferences, so in fake runs of F–H the strategy job's
`preferences_honoured` check fails, and tests must not assert otherwise.
Tests for choose rule 3 script a `StrategyAnswer` with the matching
preferences.

### Success Criteria:

#### Automated Verification:
- [ ] `cd app/backend && uv run pytest` passes
- [ ] `tests/test_v2_core.py`:
  - each side and level changes placement as specified on a tiny order
  - with no preferences, placements are identical to today's (Samples A–E)
  - `preference_report` counts
  - each job's checks passing and failing
  - `call_parsed` / `call_json` error mapping (cut-off, content filter, schema, refusal, API error, config error re-raised; usage kept)
  - each fallback
  - each choose rule
  - `run_v2` with FakeClient on A–E
  - cancel produces 4 jobs
- [ ] `tests/test_prompt_versions.py` passes

#### Manual Verification:
- [ ] None (covered by tests)

**Implementation Note**: After this phase passes automated verification,
continue to Phase 2 (same agent).

---

## Phase 2: Backend app (v2 API, storage, evaluation data)

### Overview
Generic run manager with a shared slot, v2 storage including per-job call
rows and feedback, v2 contract and routes, config, and instruction samples.

### Changes Required:

#### 1. Generic RunManager with a shared slot
**Files**: `app/backend/app/runs.py`, `app/backend/app/main.py`, `app/backend/app/routes.py`
**Changes**:
- `class Slot` holds `lock` and `active: ActiveRun | None`. `ActiveRun` gains
  `kind: str`.
- `RunManager(adapter=V1_ADAPTER, slot=None, kind="v1")`. With no arguments it
  behaves exactly as today, so `main.py` and `tests/test_sse.py` keep working.
- `adapter` is an object with:
  - `pipeline(req, client, *, run_id, meta, emit, should_stop, partial)`
  - `new_partial()`, `partial_result(partial)`
  - `create_run(run_id, req, meta)`
  - `finish(run_id, status, error, result, events) -> bool`
  - `set_status`, `append_event`, `mark_interrupted`
  - `cancelled_result(exc, partial)`: v2 copies `RunCancelledV2.jobs` into
    the result
  - `error_result(exc, partial)`: v2 copies `ConfigErrorV2.jobs`
  - `outcome(result) -> (status, error)`:
    - v1 keeps today's rule (`final is None` → `failed`, "No valid plan from
      the model or the packer")
    - v2 always returns `("succeeded", None)` when the pipeline returns
  - `title(req)`: v1 and v2 both use the first three item names

  `V1_ADAPTER` wraps today's functions, and its `finish` pops `svgs` /
  `history` as today. `V2_ADAPTER.finish` pops `svgs` and `jobs` from the
  result and calls `store_v2.finish(…, jobs)`. The `set_status` fallback
  (`runs.py:203`) uses the adapter's `set_status`.
- Every method that locks today (`start`, `cancel`, `active_id`,
  `get_active`, `events_from`, `_emit`, `_finish`) uses `slot.lock`.
  `active_id`, `get_active` and `cancel` only see `slot.active` when its
  `kind` matches.
- `client_factory` is per manager. v2 tests set
  `app.state.runs_v2.client_factory`.
- `start` raises `RunBusy(kind=slot.active.kind)` for either kind.
  - Both routes return `409 {"detail": "A run is already in progress", "kind": kind}`.
  - The detail text is unchanged from v1, so existing v1 tests and
    `App.test.tsx` still pass.
- Lifespan (`main.py`):
  - startup: `store.init(); store_v2.init(); store.mark_interrupted(); store_v2.mark_interrupted()`
  - `app.state.runs = RunManager(slot=slot)`
  - `app.state.runs_v2 = RunManager(V2_ADAPTER, slot, "v2")`
  - shutdown: call `app.state.runs.shutdown()` once. `RunManager.shutdown`
    cancels and joins whatever run is in the slot, of either kind, and no
    longer marks a store itself. The lifespan then calls
    `store.mark_interrupted()` and `store_v2.mark_interrupted()`.
- CORS `allow_methods` adds `"PUT"` (`main.py:55`).

#### 2. v2 storage
**File**: `app/backend/app/store_v2.py` (same SQLite file, `config.DB_PATH`, read at call time; WAL; `executescript` in `init()`)
```sql
CREATE TABLE IF NOT EXISTS runs_v2 (
  run_id TEXT PRIMARY KEY, trace_id TEXT, request_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','cancelled','interrupted')),
  error TEXT, created_at TEXT NOT NULL, finished_at TEXT,
  client_ip TEXT, user_agent TEXT, title TEXT,
  fake INTEGER NOT NULL DEFAULT 0,
  source TEXT, reason TEXT,
  total_cost REAL NOT NULL DEFAULT 0, total_s REAL NOT NULL DEFAULT 0,
  over_budget INTEGER NOT NULL DEFAULT 0,
  request_json TEXT NOT NULL, result_json TEXT, svgs_json TEXT,
  events_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS runs_v2_created ON runs_v2 (created_at);
CREATE TABLE IF NOT EXISTS job_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  job TEXT NOT NULL CHECK (job IN ('extract','strategy','check','notes')),
  status TEXT NOT NULL CHECK (status IN ('ok','fallback','skipped')),
  fake INTEGER NOT NULL DEFAULT 0,
  model TEXT, reasoning_effort TEXT, prompt_version TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  checks_json TEXT NOT NULL DEFAULT '[]',
  checks_passed INTEGER NOT NULL DEFAULT 0, checks_total INTEGER NOT NULL DEFAULT 0,
  prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0, latency_s REAL NOT NULL DEFAULT 0,
  error TEXT, answer_json TEXT, output_json TEXT, messages_json TEXT,
  created_at TEXT NOT NULL   -- the run's finished_at, so `since` filters match runs
);
CREATE UNIQUE INDEX IF NOT EXISTS job_calls_run_job ON job_calls (run_id, job);
CREATE INDEX IF NOT EXISTS job_calls_group ON job_calls (job, prompt_version, model, created_at);
CREATE TABLE IF NOT EXISTS feedback (
  run_id TEXT NOT NULL,
  job TEXT NOT NULL CHECK (job IN ('extract','strategy','check','notes')),
  rating TEXT CHECK (rating IN ('up','down')),
  comment TEXT NOT NULL DEFAULT '',
  wrong_verdicts_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, job)
);
```
- `create_run` writes the `runs_v2` row, with `title` from the first three
  item names and `fake`.
- `finish(run_id, status, error, result, events, jobs)`:
  - compute `ts = now_iso()` once
  - in one transaction, first
    `UPDATE runs_v2 SET …, finished_at=ts … WHERE run_id=? AND status='running'`
  - only if `rowcount == 1`, insert the job rows with
    `INSERT … ON CONFLICT(run_id, job) DO NOTHING`, `created_at = ts` and the
    run's `fake`
  - return `wrote`
- These runs never get job rows (documented):
  - interrupted runs
  - runs whose `finish` failed twice and fell back to `set_status`
- Skipped job rows still record the *configured* model and effort, so their
  `skipped` count lands in the right evaluation group.
- The check job's `output_json` is its verdict list (`[{index, verdict, reason}]`).
- `append_event` is guarded the same way as in v1. `events(run_id)` has the
  same shape as v1's.
- `upsert_feedback(run_id, job, fb)`:
  - `INSERT … SELECT … WHERE EXISTS (SELECT 1 FROM job_calls WHERE run_id=? AND job=?)`
    `ON CONFLICT(run_id, job) DO UPDATE SET rating, comment, wrong_verdicts_json, updated_at`
  - `created_at` is untouched on update
  - returns `None` when the job row doesn't exist; the route returns 404
- `eval_rows(job, since, include_fake)`:
  - one SQL `SELECT` (excluding `messages_json`) over `job_calls`, with
    `LEFT JOIN feedback` and `LEFT JOIN runs_v2` (for `source` / `status`),
    filtered by job / since / fake
  - Python groups by `(job, prompt_version, model, reasoning_effort)`, with
    `None` as a key value
  - Python computes the rates as defined in `EvalGroup` (skipped excluded,
    `None` when there are 0 calls), the nearest-rank p50/p95, and the
    per-check shares from `checks_json`
  - 👍/👎 counts exclude skipped rows. Feedback on a skipped job is rejected
    at the route.
  - `wrong_verdict_rate` = sum of `len(wrong_verdicts)` / sum of
    `len(output verdicts)`, over check rows with `status == "ok"` that have a
    feedback row (a rating or a comment). `None` when that denominator is 0.
  - `model_won_rate` = share of strategy rows with `status != "skipped"`
    whose run is `succeeded` with `source == "model"`. The denominator is
    those rows in succeeded runs; `source "none"` counts as not won.
  - Runs that never finished (interrupted, or the `set_status` fallback) have
    no rows, so they are invisible to evaluation.
  - `since` is normalised to millisecond ISO-Z (`…:00Z` → `…:00.000Z`) before
    comparing, so rows in the first second aren't dropped.
- Replay: `job_calls.messages_json` + `runs_v2.result_json` (settings,
  prepared items and boxes, packer plan) are enough to replay strategy, check
  and notes offline. Keep the result shape stable.

#### 3. Contract models
**File**: `app/backend/app/schemas_v2.py`
**Changes**:
- Implement the Output contract above exactly.
- `OrderInput` moves into `app/schemas.py` as the base of `RunRequest`, with
  no behaviour change. It keeps `_STRICT` (`allow_inf_nan=False`) and does
  **not** forbid extra keys, so v1 still accepts unknown keys as it does
  today. `extra="forbid"` is set only on `RunRequestV2`, `JobChoice` and
  `FeedbackIn`.
- `Sample.key` grows to `Literal["A".."H"]`.

#### 4. v2 pipeline adapter
**File**: `app/backend/app/pipeline_v2.py`
**Changes**:
- `V2_ADAPTER` and `run_pipeline_v2(...)`.
- Wraps `packing.v2.run_v2` in `agents.trace`, with v1's metadata plus
  `fake`.
- Builds the `JobConfig`s from the request and `config.JOBS`. A job whose
  model has `kwargs` in `EXTRACTION_MODELS` gets them in `extra_kwargs`.
  Strategy max tokens come from `MAX_COMPLETION_TOKENS["planning"][effort]`.
- Passes `bounds=config.SETTINGS_BOUNDS`.
- Forwards `on_context` to `client.context` when present.
- Computes cost per job from `config.PRICES`, plus totals and `over_budget`.
- Builds `PlanSideV2`s, `comparison`, the final plan (`final_plan` incl. cut
  notes) and SVGs (final, model, packer).
- Returns `jobs` for `store_v2.finish`.
- `fake = config.FAKE_LLM or isinstance(client, FakeClient)`. When fake, set
  `client.echo_soft = True` before extraction.
- Config errors become `PipelineError` via v1's `_openai_error`.

#### 5. Config and samples
**Files**: `app/backend/app/config.py`, `app/backend/app/samples.py`
**Changes**:
```python
JOBS = {
  "extract":  {"models": [m["id"] for m in EXTRACTION_MODELS], "default": {"model": "gpt-4.1-nano", "reasoning_effort": None}, "max_tokens": 2000},
  "strategy": {"models": [m["id"] for m in PLANNING_MODELS],   "default": {"model": "gpt-5.4-nano", "reasoning_effort": "low"}},  # max tokens by effort
  "check":    {"models": [m["id"] for m in EXTRACTION_MODELS], "default": {"model": "gpt-4.1-mini", "reasoning_effort": None}, "max_tokens": 1500},
  "notes":    {"models": [m["id"] for m in EXTRACTION_MODELS], "default": {"model": "gpt-4.1-nano", "reasoning_effort": None}, "max_tokens": 1500},
}
BUDGET = {"usd": 0.02, "seconds": 30}
```
- `/api/v2/config` reports `efforts`: for strategy models, from `efforts`;
  for the others, `null`. Their fixed `kwargs` are applied server-side.
- `SAMPLES_V2` (keys F, G, H) is separate from `SAMPLES`. `/api/v2/samples`
  returns `SAMPLES + SAMPLES_V2`; `/api/samples` (v1) is unchanged at A–E.
  - **F**, heavy at the back: `"Put the cast iron pans at the back and the glass bowls at the front."`
  - **G**, jars left / kettles right: Sample E's items (Kettle ×5, Shoe Box
    ×2, Jar ×3) with boxes **Wide 10×16×26 (weightMax 60) and Medium
    10×12×16 (weightMax 50)**, plus
    `"Keep the jars on the left side and the kettles on the right."`
    - Prototype (second review): the packer honours only kettles 4/5 and
      jars 2/3.
    - A guided plan with boxes [Wide, Medium], order [Shoe Box, Kettle, Jar],
      kettles right and jars left honours 5/5 and 3/3, with equal shipped
      volume, no extra boxes and no leftovers.
    - With Sample E's own boxes the instruction can only be honoured with 3
      boxes, which is why G uses different ones.
  - **H**, fragile on top: `"The vases are fragile: put them on top of everything."`
- The implementer picks F's and H's dimensions and **tunes them until the
  gates pass**. Starting points:
  - F: 3 pans 3×10×10 and 4 bowls 4×6×6 in M 10×12×16 / L 12×16×20. Feasible
    in the prototype at a ratio of 1.11, which is tight.
  - H: start from vases that the packer leaves on the floor next to taller
    items. The first prototype attempt had the packer already putting them on
    top, so it was rejected.

  Tests in `test_v2_api.py` gate all three samples:
  - the `preference_report` of the packer plan has at least one row with
    `honoured < total`
  - `pack_guided` with the matching preferences (F: pans back, bowls front;
    G: jars left, kettles right; H: vases top) gives a plan that passes
    `validate_plan`, has every row `honoured == total`, no extra boxes or
    leftovers, and ≤ 1.15 × the packer's shipped volume

#### 6. v2 routes
**File**: `app/backend/app/routes_v2.py` (prefix `/api/v2`, registered in `main.py`; same middleware; same 404 mapping for a bad `run_id`)

| Method | Path | Body / Query | Response |
|---|---|---|---|
| GET | `/config` | — | `{jobs: {name: {default, models: [{id, label, efforts \| null, price_in, price_out}]}}, budget, caps, volume_margin}` |
| GET | `/samples` | — | A–H |
| POST | `/runs` | `RunRequestV2` | 202 `{run_id}`; 409 `{"detail": "A run is already in progress", "kind": "v1"\|"v2"}`; 422 |
| GET | `/runs` | — | `RunSummaryV2[]` (newest first, 100) |
| GET | `/runs/active` | — | `{run_id \| null}` (v2 runs only) |
| GET | `/runs/{id}` | — | `RunV2`; while running, events come from the active run (as v1 `routes.py:114-118`), and the body builder uses `{}` defaults for the empty `result_json` (as v1 `routes.py:119`) |
| GET | `/runs/{id}/events` | `?from=` / Last-Event-ID | SSE via `event_stream(runs_v2, store_v2, …)` |
| POST | `/runs/{id}/cancel` | `{}` | 202 / 409 |
| GET | `/runs/{id}/render` | `plan=final\|model\|packer&box&upto` | SVG (404 when that plan doesn't exist) |
| GET | `/runs/{id}/svg` | `plan=final\|model\|packer` | SVG download |
| GET | `/runs/{id}/jobs/{job}/messages` | — | `{messages: [...]}` |
| PUT | `/runs/{id}/feedback/{job}` | `FeedbackIn` | 200 `Feedback`; 404 unknown run or no job row; 409 run not terminal; 422 bad job, a skipped job, `wrong_verdicts` on a non-check job, or an index outside the verdicts |
| GET | `/eval/summary` | `?job=&since=<ISO Z>&include_fake=false` | `{groups: EvalGroup[]}` (`since` compared lexically against ISO-Z timestamps; a bad format returns 422) |

### Success Criteria:

#### Automated Verification:
- [ ] `cd app/backend && uv run pytest` passes; v1 tests are unchanged
- [ ] `tests/test_v2_api.py`:
  - every route and status code
  - the shared slot in both directions, including v1 `/runs/active` hiding a v2 run
  - CORS PUT
  - feedback rules
  - eval maths (null rates, nearest-rank p50/p95, fake excluded by default)
  - cancel → 4 jobs
  - interrupted on restart, with no job rows
  - finish idempotency (retry doesn't duplicate)
  - samples F–H gates
  - contract check against `RunV2`; with `UPDATE_FIXTURES=1`, it writes the
    deterministic `tests/fixtures/run_v2.json`
  - `ConfigErrorV2` → failed run that keeps its job rows
  - `outcome`: a v2 run with `final: null` is `succeeded`

#### Manual Verification:
- [ ] None here (deferred to Phase 4)

**Implementation Note**: After automated verification, hand over to
integration.

---

## Phase 3: Frontend (v2 switch, run view, Jobs panel, feedback, Evaluation page)

### Overview
v2 UI on the same page, built against the contract with a hand-written
fixture, `src/test/fixturesV2.ts`, declared with `satisfies RunV2` so contract
drift fails typecheck. v1 stays the default and its tests are unchanged.

### Changes Required:

#### 1. API client, types and subscribe options
**Files**:
- `app/frontend/src/api/v2.ts`, `app/frontend/src/api/typesV2.ts`
- `app/frontend/src/api/client.ts`, `app/frontend/src/lib/events.ts`

**Changes**:
- `typesV2.ts` follows the Output contract.
- `v2.ts` exports `getConfigV2`, `getSamplesV2`, `createRunV2`,
  `listRunsV2`, `getActiveRunV2`, `getRunV2`, `cancelRunV2`, `subscribeV2`,
  `renderUrlV2`, `svgUrlV2`, `getJobMessages`, `putFeedback`,
  `getEvalSummary`.
- `subscribe<R extends {status: RunStatus; events: E[]}, E extends {id: number; type: string}>(runId, onEvent: (e: E) => void, onClosed: (run: R | null) => void, fromId, opts?)`,
  where `opts = {base?: string, getRun?: (id) => Promise<R>, types?: string[]}`.
  - The defaults are v1's (`Run`, `RunEvent`, `getRun`, `EVENT_TYPES`), so v1
    calls and `client.test.ts` are unchanged.
  - `subscribeV2` passes `/api/v2`, `getRunV2` and the v2 event types.
- Export the module-private helpers (`send`, `request`, `jsonPost`, `enc`,
  `BASE`) from `client.ts`, and add `jsonPut`. `v2.ts` reuses them, and
  `ApiError` for inline 422s.
- `formatEventV2` in `events.ts` for `started`, `job`, `packer`, `chosen` and
  `end`.

#### 2. Generic run flow, two instances
**File**: `app/frontend/src/hooks/useRunFlow.ts`
**Changes**:
- `useRunFlow<R extends BaseRun, E extends BaseEvent>(onFinished, api?)`,
  where `BaseRun = {run_id, status, events}` and `BaseEvent = {id, type}`.
- `v1Api = {getRun: (id) => getRun(id), subscribe: (...a) => subscribe(...a), cancelRun: (id) => cancelRun(id)}`
  is defined **in this module**, as lazy wrappers over the imports, so
  `vi.mock('./api/client')` in `App.test.tsx` still applies.
- `App` calls `useRunFlow` twice: `flowV1` with `onFinished` refreshing the
  v1 history, and `flowV2` refreshing the v2 history.
  - `running = flowV1.running || flowV2.running` disables Pack in both.
  - Cancel and `RunControls` use the active pipeline's flow.

#### 3. PlanPanel props and reuse
**Files**: `PlanPanel.tsx`, `HistoryList.tsx`
**Changes**:
- `PlanPanel` gets optional `renderSrc(box, upto?)`, `downloadHref` and
  `label`, and `planKey` is widened to `'model' | 'packer' | 'final'`.
  - By default it builds v1 URLs and labels exactly as today, so the
    `Results.test.tsx` assertions stay unchanged.
  - For the final plan, `RunViewV2` passes
    `{ok: true, plan: final.plan, box_count: final.plan.boxes.length, rank: null, errors: [], hints: []}`.
- `HistoryList` becomes generic over
  `{run_id, created_at, title, status}`, with an optional
  `renderMeta(run) => ReactNode` that replaces the model and status lines.
  - Its defaults stay identical to v1.
  - v2 shows `$cost · source · reason · over budget`.
- `SamplePicker` becomes generic over `{key: string; title; description}`.
  The v1 `Sample` type is unchanged.
- **Not reused for v2:**
  - `PromptsPanel`: replaced by JobCard's "Show messages"
  - `PlansSideBySide`: `RunViewV2` lays out two `PlanPanel`s itself
  - `RunView`
  - `RunControls`' event formatting: `RunControls` gets a `format` prop,
    defaulting to v1's, and drops the packer wait hint for v2

#### 4. Header: pipeline switch and tabs
**File**: `app/frontend/src/App.tsx`
**Changes**:
- `Pipeline: v1 | v2` (`aria-pressed` buttons). **The default is v1** in this
  phase. The choice is kept in `localStorage` inside try/catch.
  `test/setup.ts` clears `localStorage` after each test.
- `Playground | Evaluation` tabs (`role=tablist/tab/tabpanel`): the editor
  stays mounted when the Evaluation tab is shown.
- Bootstrap loads v1 config and samples. v2 config and samples load the first
  time v2 is selected, so existing tests that mock only v1 calls are
  unaffected.
- Per pipeline:
  - samples (`SampleV2` from `/api/v2/samples`)
  - settings (`ModelChoice` for v1, `JobChoices` for v2, both kept across
    switches)
  - history
  - `pastRun`, cleared on switch
- Active-run attach on mount:
  - v1 is checked as today.
  - v2 is checked only when v2 is the selected pipeline, or when the v1
    check returned `null`. Failures are swallowed silently, with no alert and
    no shared error state, so existing App tests, which mock only v1, are
    unaffected.
- A 409 keeps today's heading text ("A run is already in progress"). It adds
  a second line "(a v1/v2 run)" from the body's `kind`. "Show it" switches to
  that pipeline and attaches to its active run.
- Editor caps come from the v1 config, which has the same caps.

#### 5. JobSettings
**File**: `app/frontend/src/components/JobSettings.tsx`
**Changes**:
- One line per job with its default model.
- An "Advanced" `<details>` with a model select per job, plus an effort
  select only when the model has `efforts`. Changing the model resets an
  unsupported effort.
- "Reset to defaults".
- The budget line.
- `createRunV2` sends all four jobs.

#### 6. RunViewV2 and its parts
**Files**: `RunViewV2.tsx`, `JobsPanel.tsx`, `JobCard.tsx`, `FeedbackControls.tsx`, `PreferencesTable.tsx`, `VerdictsList.tsx`, `NotesSteps.tsx`
**Changes**:
- `RunViewV2`, sections in order:
  1. Run: status, totals vs budget with an "Over budget" badge in text, and
     winner + reason. `final: null` shows "No valid plan".
  2. Final plan: a `PlanPanel` keyed by run id, using `renderUrlV2(…, "final")`,
     plus `NotesSteps` per box and the cut notes.
  3. Jobs: `JobsPanel`. While the run is going, it shows the `job` events as
     progress, and an empty panel is not an error. A terminal run with
     `jobs: []` shows "No job data".
  4. Plans: model vs packer, two `PlanPanel`s.
  5. Metrics: `MetricsTable`.
  6. Preferences (model and packer side by side) and Verdicts.
  7. Extracted settings: `SettingsPanel`.
- `JobCard` shows:
  - status (text)
  - model, effort, prompt version
  - checks as `✓ name — detail` / `✗ name — detail`
  - cost, latency, calls, tokens, error
  - output in a `<details>`
  - "Show messages" (lazy `getJobMessages`)
  - `FeedbackControls`, disabled until the run is terminal
- `FeedbackControls`:
  - "Good" / "Bad" toggles (`aria-pressed`, emoji `aria-hidden`)
  - a comment field (≤ 2000)
  - Save → `putFeedback`
  - on success it updates that job's `feedback` in local state and shows
    "Saved"; errors (incl. 422) show inline
- `VerdictsList`: each verdict gets a "Mark wrong" toggle, labelled with the
  instruction. The toggles feed `wrong_verdicts` in the check job's feedback.

#### 7. Evaluation page
**File**: `app/frontend/src/components/EvaluationPage.tsx`
**Changes**:
- A job filter, an "Include fake runs" checkbox (off), and Refresh.
- Loading (`role="status"`) and error (`role="alert"`) states.
- One table per job, rows from `EvalGroup`:
  - `null` rates show "—"
  - strategy shows "Model won % (run-level)"
  - check shows "Verdicts marked wrong %"
  - per-check rates go in a `<details>`
- Empty state: "No v2 runs yet".
- Tables have a caption and `th scope`.

### Success Criteria:

#### Automated Verification:
- [ ] `cd app/frontend && npm run typecheck && npm test` passes, with existing tests unchanged
- [ ] New tests:
  - `subscribe` options (base, getRun, types)
  - two flows don't interfere
  - switch and tabs (`localStorage` resilience)
  - `JobSettings` → request body
  - `RunViewV2` renders `fixturesV2` with all sections, and with `final: null`
  - `JobCard` states
  - `FeedbackControls` requests and errors
  - `VerdictsList` toggles → `wrong_verdicts`
  - `EvaluationPage` groups, null rates, empty, loading, error
  - `PlanPanel` defaults unchanged

#### Manual Verification:
- [ ] Deferred to Phase 4

---

## Phase 4: Integration, default flip, docs, verification

### Overview
Wire end to end with the fake LLM, sync the fixture, flip the default to v2,
document, then a short real-model check within budget.

### Changes Required:

#### 1. Fixture sync and default flip
**Files**: `app/frontend/src/test/fixturesV2.ts`, `app/frontend/src/App.tsx`, `app/frontend/src/App.test.tsx`, `app/backend/tests/test_v2_api.py`
**Changes**:
- `fixturesV2.ts` imports `run_v2.json`, copied to `src/test/run_v2.json`,
  and casts it (`as unknown as RunV2`), because JSON imports widen string
  literals.
- Add `"resolveJsonModule": true` to `app/frontend/tsconfig.app.json`.
- A backend test compares the two JSON files. Both are committed and
  deterministic.
- The default pipeline becomes v2. Existing v1 App tests set
  `localStorage.pipeline = "v1"` in `beforeEach`.

#### 2. Smoke script
**File**: `app/backend/scripts/smoke_v2.py` (new directory)
**Changes**:
- Sets `PLAYGROUND_FAKE_LLM=1` and `PLAYGROUND_DB` to a temporary file.
- Uses `TestClient(app, base_url="http://localhost")`.
- Posts samples A–H to v2 and A–E to v1, and waits for each to be terminal.
- Asserts:
  - status `succeeded`
  - v2 has 4 job rows with `fake = 1`
  - render returns SVG for each final box
  - feedback PUT, then `GET /runs/{id}` shows it in `jobs[].feedback`
  - the eval summary with `include_fake=true` has 4 jobs; without it, 0

#### 3. Docs
**Files**:
- `app/backend/packing/CLAUDE.md`: a new "Pipeline v2" section — jobs,
  checks, choose rule, preferences, prompt versions, replay
- `app/CLAUDE.md`: the v2 API, storage, the Evaluation page, the shared slot,
  fake runs
- root `CLAUDE.md`: a downlink line for v2

### Success Criteria:

#### Automated Verification:
- [ ] `cd app/backend && uv run python scripts/smoke_v2.py` exits 0
- [ ] `cd app/backend && uv run pytest` and `cd app/frontend && npm run typecheck && npm test` pass

#### Manual Verification:
- [ ] Start the backend: `cd app/backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8765 --timeout-graceful-shutdown 5`
- [ ] Start the frontend: `cd app/frontend && API_TARGET=http://127.0.0.1:8765 npm run dev`
- [ ] **v2, Sample G, real default models:**
  - the run finishes in ≤ 30 s and ≤ $0.02
  - all 4 job cards show checks
  - the preferences table shows the packer failing the instruction
  - the final plan has notes
  - 👍/👎 and the comment persist after a reload
  - "Mark wrong" on a verdict persists
- [ ] **v2, Sample A (no instructions):** extract and check show "skipped",
  and the winner reason is a metrics reason
- [ ] **Bad instructions** ("pad everything 50 inches"): extract shows
  `fallback` with `in_bounds ✗`, and the run still succeeds
- [ ] **Shared slot:** starting a v1 run while a v2 run is going gives "A v2
  run is in progress", and v1's active run stays empty
- [ ] **Evaluation page** after 3–5 real runs:
  - rows per job
  - fake runs hidden by default
  - strategy shows the run-level model won %
- [ ] **v1** still works for A–E

**Implementation Note**: Pause after the manual verification for the user's
confirmation.

---

## Testing Strategy

### Unit Tests (backend):
- **Preferences:**
  - each side and level on a tiny order
  - wall-anchored points used
  - `top` takes the highest valid spot
  - no preferences → placements identical to today's (Samples A–E)
- **`preference_report`:** every definition, including "on top" with stacked top units
- **Jobs:**
  - each check passes and fails
  - `call_parsed` / `call_json` error mapping, with usage kept
  - fallbacks
  - skipped conditions
  - `settings_problems` with bounds as a parameter
- **`choose`:** rules 1–4, including
  - the packer already honouring the instructions → metrics decide
  - the model plan with no preference rows → rule 3 doesn't apply
  - 1.15 vs 1.16
  - an extra box or an extra leftover
  - a `cannot_tell` verdict
  - a metrics tie → model
  - neither side valid
- **`run_v2`:**
  - FakeClient on A–H via `on_context` (with `echo_soft`, so check runs on F–H)
  - a scripted strategy with preferences on G wins on the instructions (rule 3)
  - `RunCancelledV2` → 4 jobs
  - a config error → `ConfigErrorV2` with 4 jobs (finished + "not run")
- **Prompt-version pinning**

### Integration Tests (backend API):
- every v2 route and code
- the shared slot both ways, and `kind` filtering of active and cancel
- finish idempotency; interrupted runs have no job rows
- feedback rules (no job row, run not terminal, `wrong_verdicts` validation)
- eval maths, including the `since` filter and fake exclusion
- the contract fixture

### Frontend Tests:
- `subscribe` options; the two flows; switch and tabs
- `JobSettings`
- `RunViewV2`, including `final: null`
- `JobCard`, `FeedbackControls`, `VerdictsList`
- `EvaluationPage` states
- `PlanPanel` defaults

### Manual Testing Steps:
1. Run v2 on G and H with the defaults; check the winner reason, the preferences (both sides) and the verdicts.
2. 👎 plus a comment on strategy, and "Mark wrong" on a verdict; reload; confirm both persist and show on the Evaluation page.
3. Override strategy to gpt-5.4-mini / low; confirm a separate Evaluation row.
4. Run with the fake LLM; confirm the Evaluation page hides it until "Include fake runs" is ticked.

## Performance Considerations
- Preferences add a few candidate points per unit; this is negligible
  compared with today's `_fill_box`. Nothing else changes in the packer.
- Each run is 2–5 model calls. The defaults are expected to fit $0.02 / 30 s,
  and totals are flagged when they don't.
- `job_calls` grows by 4 rows per run. Eval reads exclude `messages_json`.
  Python aggregation is fine at playground scale.

## Migration Notes
- New tables are created with `CREATE TABLE IF NOT EXISTS` in the same
  `runs.db`. v1 data is untouched.
- Later schema changes need an explicit `ALTER TABLE` in `init()`, because
  there is no migration framework.
- No backfill. No v1 behaviour changes; the v1 409 body only gains a `kind` field.

## References
- This session's analyses and the dry-run review (5 reviewers, 2026-10-05)
- Handoff: `ai-tools/thoughts/shared/handoffs/general/2026-10-05_00-54-41_packing-playground-and-single-source.md`
- Previous plan: `docs/plans/2026-10-04-packing-playground.md`
- Docs: `app/backend/packing/CLAUDE.md`, `app/CLAUDE.md`
- Code:
  - `packing/packer.py` (`pack_guided`, `_fill_box` `:104`)
  - `packing/planner.py` (`_ask` `:156`, `_CONFIG_ERRORS` `:128`, `_rank` `:209`, `_coverage_errors` `:216`, `_describe` `:249`, `guidance_from_plan` `:414`, `_guidance_feedback` `:428`)
  - `packing/settings.py` (`extract_settings` `:182`, `_SCHEMA` `:232`, `_check_ids` `:359`)
  - `app/pipeline.py` (`_check_settings` `:163`, context `:313`)
  - `app/runs.py:47`, `app/sse.py:47`, `app/main.py:46,55`, `app/schemas.py:108-130`
  - `app/frontend/src/api/client.ts:124-216`, `hooks/useRunFlow.ts`, `components/PlanPanel.tsx`
