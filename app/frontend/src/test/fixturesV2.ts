// Hand-written v2 fixtures, declared `satisfies` the contract types so that
// drift fails typecheck. Phase 4 replaces `runV2` with the backend fixture
// (app/backend/tests/fixtures/run_v2.json).
import type {
  ConfigV2,
  EvalGroup,
  JobView,
  RunEventV2,
  RunSummaryV2,
  RunV2,
  SampleV2,
} from '../api/typesV2'
import { makePlan, okSide, sampleA } from './fixtures'

export const RUN_V2_ID = '00000000-0000-4000-8000-000000000001'

const plan = makePlan()
const finalPlan = { ...plan, notes: ['Cut box 2 to 6 in.'] }

const tokens = { prompt: 100, completion: 50, reasoning: 0 }

export const jobsV2 = [
  {
    job: 'extract',
    status: 'ok',
    model: 'gpt-4.1-nano',
    reasoning_effort: null,
    prompt_version: 'extract-v1',
    calls: 1,
    checks: [
      { name: 'parsed', passed: true, detail: '' },
      { name: 'known_ids', passed: true, detail: '' },
    ],
    output: { soft_instructions: ['Keep the jars on the left'] },
    answer: { soft_instructions: ['Keep the jars on the left'] },
    error: null,
    cost: 0.0001,
    latency_s: 1,
    tokens,
    feedback: null,
  },
  {
    job: 'strategy',
    status: 'ok',
    model: 'gpt-5.4-nano',
    reasoning_effort: 'low',
    prompt_version: 'strategy-v1',
    calls: 1,
    checks: [
      { name: 'parsed', passed: true, detail: '' },
      { name: 'preferences_honoured', passed: false, detail: 'Jar 2/3' },
    ],
    output: { boxes: [1, 2], order: [1, 2], rotations: [], preferences: [], reason: 'ok' },
    answer: { boxes: [1, 2], order: [1, 2], rotations: [], preferences: [], reason: 'ok' },
    error: null,
    cost: 0.002,
    latency_s: 1,
    tokens: { prompt: 1000, completion: 400, reasoning: 200 },
    feedback: null,
  },
  {
    job: 'check',
    status: 'ok',
    model: 'gpt-4.1-mini',
    reasoning_effort: null,
    prompt_version: 'check-v1',
    calls: 1,
    checks: [
      { name: 'parsed', passed: true, detail: '' },
      { name: 'covers_all', passed: true, detail: '' },
    ],
    output: { verdicts: [] },
    answer: { verdicts: [] },
    error: null,
    cost: 0.0005,
    latency_s: 1,
    tokens,
    feedback: null,
  },
  {
    job: 'notes',
    status: 'fallback',
    model: 'gpt-4.1-nano',
    reasoning_effort: null,
    prompt_version: 'notes-v1',
    calls: 1,
    checks: [{ name: 'step_count', passed: false, detail: 'box 2 has 9 steps' }],
    output: { boxes: [] },
    answer: null,
    error: 'ValidationError: bad notes',
    cost: 0.0001,
    latency_s: 1,
    tokens,
    feedback: null,
  },
] satisfies JobView[]

export const eventsV2 = [
  { id: 0, t: 0, type: 'started' },
  { id: 1, t: 1, type: 'job', job: 'extract', status: 'ok', passed: 2, total: 2 },
  { id: 2, t: 2, type: 'packer', ok: true, boxes: 2 },
  { id: 3, t: 3, type: 'job', job: 'strategy', status: 'ok', passed: 1, total: 2 },
  { id: 4, t: 4, type: 'chosen', source: 'model', reason: 'honours the instructions' },
  { id: 5, t: 5, type: 'end', status: 'succeeded', error: null },
] satisfies RunEventV2[]

export const runV2 = {
  run_id: RUN_V2_ID,
  status: 'succeeded',
  error: null,
  created_at: '2026-01-01T00:00:00.000Z',
  finished_at: '2026-01-01T00:00:05.000Z',
  fake: false,
  request: {
    items: sampleA.items,
    boxes: sampleA.boxes,
    instructions: 'Keep the jars on the left side and the kettles on the right.',
    jobs: {
      extract: { model: 'gpt-4.1-nano', reasoning_effort: null },
      strategy: { model: 'gpt-5.4-nano', reasoning_effort: 'low' },
      check: { model: 'gpt-4.1-mini', reasoning_effort: null },
      notes: { model: 'gpt-4.1-nano', reasoning_effort: null },
    },
  },
  events: eventsV2,
  settings: {
    padding: 0.125,
    padding_by_item: [],
    blocks: [],
    upright: [],
    fixed_orientation: [],
    no_stack_on: [],
    max_load_on_top: [],
    floor_only_over_weight: null,
    wall_clearance: 0,
    cut_step: null,
    min_cut_height: null,
    carrier_max_weight: null,
    carrier_max_length: null,
    carrier_max_girth: null,
    min_fill: null,
    max_boxes: null,
    keep_together: [],
    keep_apart: [],
    balance_boxes: false,
    soft_instructions: ['Keep the jars on the left side and the kettles on the right.'],
  },
  prepared_items: [
    {
      id: 1,
      source_id: 1,
      name: 'Mug',
      color: 'teal',
      weight: 0.9,
      dimensions: { x: 4.75, y: 4, z: 4 },
      quantity: 4,
    },
  ],
  prepared_boxes: [{ id: 1, name: 'M-12', weightMax: 50, dimensions: { x: 8, y: 12, z: 12 } }],
  jobs: jobsV2,
  packer: okSide(plan),
  model: okSide(plan, [0, 2, 900, 20]),
  source: 'model',
  reason: 'honours the instructions',
  final: {
    source: 'model',
    reason: 'honours the instructions',
    plan: finalPlan,
    notes: [
      { box: 1, steps: ['Load 3 × Mug', 'Close the lid'] },
      { box: 2, steps: ['Load 2 × Mug'] },
    ],
    cut_notes: ['Cut box 2 to 6 in.'],
  },
  preferences_model: [
    {
      id: 1,
      name: 'Mug',
      side: 'left',
      level: 'any',
      honoured: 3,
      total: 3,
      detail: '3/3 on the left half',
    },
  ],
  preferences_packer: [
    {
      id: 1,
      name: 'Mug',
      side: 'left',
      level: 'any',
      honoured: 1,
      total: 3,
      detail: '1/3 on the left half',
    },
  ],
  verdicts: [
    {
      index: 0,
      instruction: 'Keep the jars on the left side',
      verdict: 'honoured',
      reason: 'All jars are on the left',
    },
    {
      index: 1,
      instruction: 'kettles on the right',
      verdict: 'cannot_tell',
      reason: 'No kettle placement given',
    },
  ],
  comparison: [
    {
      metric: 'boxes',
      label: 'Boxes used',
      unit: '',
      direction: 'lower',
      model: 2,
      packer: 2,
      delta: 0,
      verdict: 'same',
    },
  ],
  totals: {
    cost: 0.0027,
    seconds: 5,
    over_budget: false,
    budget: { usd: 0.02, seconds: 30 },
  },
} satisfies RunV2

export function makeRunV2(over: Partial<RunV2> = {}): RunV2 {
  return { ...runV2, ...over }
}

/** A terminal run where neither side had a valid plan. */
export function makeNoPlanRunV2(): RunV2 {
  return makeRunV2({
    source: 'none',
    reason: 'no valid plan',
    final: null,
    packer: { ok: false, plan: null, box_count: 0, rank: null, errors: ['too heavy'], hints: [] },
    model: { ok: false, plan: null, box_count: 0, rank: null, errors: [], hints: [] },
    preferences_model: [],
    preferences_packer: [],
    verdicts: [],
    comparison: null,
  })
}

export const summariesV2: RunSummaryV2[] = [
  {
    run_id: RUN_V2_ID,
    created_at: '2026-01-01T00:00:00.000Z',
    finished_at: '2026-01-01T00:00:05.000Z',
    status: 'succeeded',
    title: 'Mug, Dinner Plate, Candle Jar',
    source: 'model',
    reason: 'honours the instructions',
    total_cost: 0.0027,
    total_s: 5,
    over_budget: true,
    fake: false,
  },
]

export const configV2: ConfigV2 = {
  jobs: {
    extract: {
      default: { model: 'gpt-4.1-nano', reasoning_effort: null },
      models: [
        { id: 'gpt-4.1-nano', label: 'GPT-4.1 nano', efforts: null, price_in: 0.1, price_out: 0.4 },
        { id: 'gpt-4.1-mini', label: 'GPT-4.1 mini', efforts: null, price_in: 0.4, price_out: 1.6 },
      ],
    },
    strategy: {
      default: { model: 'gpt-5.4-nano', reasoning_effort: 'low' },
      models: [
        {
          id: 'gpt-5.4-nano',
          label: 'GPT-5.4 nano',
          efforts: ['low', 'medium', 'high'],
          price_in: 0.2,
          price_out: 1.25,
        },
        { id: 'gpt-4.1-mini', label: 'GPT-4.1 mini', efforts: null, price_in: 0.4, price_out: 1.6 },
      ],
    },
    check: {
      default: { model: 'gpt-4.1-mini', reasoning_effort: null },
      models: [
        { id: 'gpt-4.1-nano', label: 'GPT-4.1 nano', efforts: null, price_in: 0.1, price_out: 0.4 },
        { id: 'gpt-4.1-mini', label: 'GPT-4.1 mini', efforts: null, price_in: 0.4, price_out: 1.6 },
      ],
    },
    notes: {
      default: { model: 'gpt-4.1-nano', reasoning_effort: null },
      models: [
        { id: 'gpt-4.1-nano', label: 'GPT-4.1 nano', efforts: null, price_in: 0.1, price_out: 0.4 },
        { id: 'gpt-4.1-mini', label: 'GPT-4.1 mini', efforts: null, price_in: 0.4, price_out: 1.6 },
      ],
    },
  },
  budget: { usd: 0.02, seconds: 30 },
  caps: { max_item_rows: 20, max_box_rows: 10, max_units: 150, max_instruction_chars: 2000 },
  volume_margin: 1.15,
}

export const samplesV2: SampleV2[] = [
  sampleA,
  {
    key: 'G',
    title: 'Jars left, kettles right',
    description: 'Instruction sample',
    items: sampleA.items,
    boxes: sampleA.boxes,
    instructions: 'Keep the jars on the left side and the kettles on the right.',
  },
]

export const evalGroups: EvalGroup[] = [
  {
    job: 'strategy',
    prompt_version: 'strategy-v1',
    model: 'gpt-5.4-nano',
    reasoning_effort: 'low',
    calls: 10,
    skipped: 0,
    ok_rate: 0.8,
    fallback_rate: 0.2,
    all_checks_rate: 0.5,
    per_check: { parsed: 1, plan_valid: 0.9 },
    thumbs_up: 3,
    thumbs_down: 1,
    wrong_verdict_rate: null,
    model_won_rate: 0.4,
    avg_cost: 0.002,
    p50_latency_s: 4,
    p95_latency_s: 9,
    first_seen: '2026-01-01T00:00:00.000Z',
    last_seen: '2026-01-02T00:00:00.000Z',
  },
  {
    job: 'check',
    prompt_version: 'check-v1',
    model: 'gpt-4.1-mini',
    reasoning_effort: null,
    calls: 0,
    skipped: 4,
    ok_rate: null,
    fallback_rate: null,
    all_checks_rate: null,
    per_check: {},
    thumbs_up: 0,
    thumbs_down: 0,
    wrong_verdict_rate: 0.25,
    model_won_rate: null,
    avg_cost: null,
    p50_latency_s: null,
    p95_latency_s: null,
    first_seen: '2026-01-01T00:00:00.000Z',
    last_seen: '2026-01-02T00:00:00.000Z',
  },
]
