import type {
  Config,
  InputBox,
  InputItem,
  Plan,
  PlanSide,
  Run,
  RunEvent,
  RunSummary,
  Sample,
} from '../api/types'

export const config: Config = {
  extraction_models: [
    { id: 'gpt-4.1-nano', label: 'GPT-4.1 nano', price_in: 0.1, price_out: 0.4, reasoning: false },
    { id: 'gpt-5.4-nano', label: 'GPT-5.4 nano', price_in: 0.2, price_out: 1.25, reasoning: true },
  ],
  planning_models: [
    {
      id: 'gpt-5.4-nano',
      label: 'GPT-5.4 nano',
      price_in: 0.2,
      price_out: 1.25,
      efforts: ['low', 'medium', 'high'],
    },
    { id: 'gpt-4.1-mini', label: 'GPT-4.1 mini', price_in: 0.4, price_out: 1.6, efforts: [] },
  ],
  defaults: {
    extraction_model: 'gpt-4.1-nano',
    planning_model: 'gpt-5.4-nano',
    reasoning_effort: 'low',
  },
  loop: { max_rounds: 3, improve_rounds: 1 },
  caps: { max_item_rows: 20, max_box_rows: 10, max_units: 150, max_instruction_chars: 2000 },
}

const item = (
  name: string,
  color: string,
  weight: number,
  x: number,
  y: number,
  z: number,
  quantity: number,
): InputItem => ({ name, color, weight, x, y, z, quantity })
const box = (
  name: string,
  weightMax: number,
  x: number,
  y: number,
  z: number,
): InputBox => ({ name, weightMax, x, y, z, stock: null })

export const sampleA: Sample = {
  key: 'A',
  title: 'Home goods',
  description: 'Mixed sizes; should fit one box',
  items: [
    item('Mug', 'teal', 0.9, 4.5, 3.75, 3.75, 4),
    item('Dinner Plate', 'ivory', 1.6, 1.25, 11, 11, 4),
    item('Candle Jar', 'goldenrod', 1.1, 4, 3.5, 3.5, 3),
    item('Cookbook', 'firebrick', 2.4, 1.5, 8, 10, 1),
    item('Kitchen Towel Set', 'lightblue', 0.7, 2.5, 6, 8, 2),
  ],
  boxes: [
    box('S-10', 40, 6, 10, 10),
    box('M-12', 50, 8, 12, 12),
    box('L-14', 60, 10, 14, 14),
    box('Flat-16', 50, 6, 12, 16),
    box('XL-18', 70, 12, 16, 18),
  ],
  instructions: 'Keep mugs upright.',
}

export const sampleB: Sample = {
  key: 'B',
  title: 'Office / electronics',
  description: 'Heavier, bulkier; likely 2+ boxes',
  items: [
    item('Monitor (boxed)', 'black', 14, 16, 6, 25, 1),
    item('Keyboard (boxed)', 'slategray', 2.2, 2, 6, 18, 2),
    item('Mouse (boxed)', 'silver', 0.4, 2.5, 4, 6, 3),
    item('Paper Ream', 'white', 5, 2.25, 8.5, 11, 5),
    item('Desk Lamp (boxed)', 'orange', 3.1, 5, 6, 15, 1),
    item('Cable Pack', 'darkgray', 0.3, 1, 3, 5, 6),
  ],
  boxes: [
    box('M-12', 50, 8, 12, 12),
    box('Long-20', 50, 8, 10, 20),
    box('L-18', 60, 12, 14, 18),
    box('Monitor-28', 40, 18, 8, 28),
    box('XL-24', 70, 16, 18, 24),
  ],
  instructions: '',
}

export const sampleC: Sample = {
  key: 'C',
  title: 'Nothing fits',
  description: 'Expect 0 boxes, everything leftover',
  items: [
    item('Rolled Rug', 'sienna', 12, 8, 8, 60, 1),
    item('Patio Umbrella', 'darkgreen', 9, 6, 6, 52, 2),
    item('Headboard', 'tan', 30, 4, 40, 58, 1),
    item('Anvil', 'black', 120, 6, 6, 10, 1),
  ],
  boxes: [
    box('S-10', 40, 6, 10, 10),
    box('M-12', 50, 8, 12, 12),
    box('L-18', 60, 12, 14, 18),
    box('XL-24', 70, 16, 18, 24),
  ],
  instructions: '',
}

export const samples: Sample[] = [sampleA, sampleB, sampleC]

/** A plan with two physical boxes: box 1 has 3 units, box 2 has 2 units. */
export function makePlan(): Plan {
  const mk = (id: number, name: string, n: number) => ({
    id,
    name,
    dimensions: { x: 8, y: 12, z: 12 },
    max_contents_weight: 50,
    items: Array.from({ length: n }, (_, k) => ({
      id: 1,
      color: 'teal',
      name: 'Mug',
      weight: 0.9,
      dimensions: { x: 4, y: 4, z: 4 },
      quantity: 1,
      packed_index: k + 1,
      placement_position: { x: 0, y: 0, z: k * 4 },
    })),
  })
  return {
    length_unit: 'in',
    weight_unit: 'lb',
    coordinate_system: { x: 'height' },
    boxes: [mk(1, 'M-12', 3), mk(2, 'L-14', 2)],
    leftover_items: [
      {
        items: [
          { id: 4, color: 'red', name: 'Anvil', weight: 120, dimensions: { x: 6, y: 6, z: 10 }, quantity: 1 },
        ],
        reason: 'Heavier than any box allows <b>bold</b>',
      },
    ],
    notes: ['Cut box 2 to 6 in.', 'Model note one'],
  }
}

export const okSide = (plan: Plan, rank: PlanSide['rank'] = [0, 2, 1000, 20]): PlanSide => ({
  ok: true,
  rank,
  errors: [],
  hints: [],
  plan,
  box_count: plan.boxes.length,
})

export const events: RunEvent[] = [
  { id: 0, type: 'started', t: 0 },
  { id: 1, type: 'extracted', t: 1.2, tokens: 300, skipped: false },
  { id: 2, type: 'prepared', t: 1.3, item_types: 5, units: 14 },
  { id: 3, type: 'packer', t: 2, ok: true, boxes: 2, errors_count: 0 },
  { id: 4, type: 'round', t: 5, n: 2, kind: 'fix', valid: false, errors_count: 5 },
  { id: 5, type: 'chosen', t: 6, source: 'packer' },
  { id: 6, type: 'end', t: 6.1, status: 'succeeded', error: null },
]

export function makeRun(over: Partial<Run> = {}): Run {
  const plan = makePlan()
  return {
    run_id: '11111111-1111-4111-8111-111111111111',
    trace_id: 'trace_11111111111141118111111111111111',
    request_id: 'req-1',
    status: 'succeeded',
    error: null,
    created_at: '2026-10-04T10:00:00Z',
    finished_at: '2026-10-04T10:00:07Z',
    client_ip: '127.0.0.1',
    user_agent: 'test',
    request: {
      items: sampleA.items,
      boxes: sampleA.boxes,
      instructions: '',
      extraction: { model: 'gpt-4.1-nano' },
      planning: { model: 'gpt-5.4-nano', reasoning_effort: 'low' },
    },
    loop: { max_rounds: 3, improve_rounds: 1 },
    settings: {
      padding: 0.125,
      padding_by_item: [],
      blocks: [],
      upright: [1],
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
      soft_instructions: ['Ship it fast <i>please</i>'],
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
        upright: true,
      },
    ],
    prepared_boxes: [
      { id: 1, name: 'M-12', weightMax: 50, dimensions: { x: 8, y: 12, z: 12 } },
    ],
    final: { source: 'packer', plan },
    model: { ...okSide(plan, [1, 3, 1200, 30]), rounds: 2, improved: false },
    packer: okSide(plan),
    stats: {
      extraction: {
        model: 'gpt-4.1-nano',
        reasoning_effort: null,
        prompt_tokens: 200,
        completion_tokens: 100,
        reasoning_tokens: 0,
        cost: 0.0001,
      },
      planning: {
        model: 'gpt-5.4-nano',
        reasoning_effort: 'low',
        prompt_tokens: 2000,
        completion_tokens: 900,
        reasoning_tokens: 400,
        cost: 0.0015,
      },
      total_tokens: 3200,
      total_cost: 0.0016,
      timings: { extract_s: 1.1, packer_s: 0.5, plan_s: 4, total_s: 6.1 },
    },
    events,
    ...over,
  }
}

export const summaries: RunSummary[] = [
  {
    run_id: '11111111-1111-4111-8111-111111111111',
    created_at: '2026-10-04T10:00:00Z',
    finished_at: '2026-10-04T10:00:07Z',
    status: 'succeeded',
    title: 'Mug, Dinner Plate, Candle Jar',
    extraction_model: 'gpt-4.1-nano',
    planning_model: 'gpt-5.4-nano',
    source: 'packer',
    total_cost: 0.0016,
    client_ip: '127.0.0.1',
  },
  {
    run_id: '22222222-2222-4222-8222-222222222222',
    created_at: '2026-10-04T09:00:00Z',
    finished_at: null,
    status: 'failed',
    title: 'Rug',
    extraction_model: 'gpt-4.1-nano',
    planning_model: 'gpt-5.4-nano',
    source: null,
    total_cost: null,
    client_ip: '127.0.0.1',
  },
]
