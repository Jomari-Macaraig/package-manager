// v2 contract types: the TypeScript equivalent of the plan's "Output — contract"
// (app/backend/app/schemas_v2.py). v1 types are reused where the plan says so.
import type {
  InputBox,
  InputItem,
  MetricRow,
  PackingSettings,
  Plan,
  PreparedBox,
  PreparedItem,
  RunStatus,
  Sample,
} from './types'

export type JobName = 'extract' | 'strategy' | 'check' | 'notes'
export type JobStatus = 'ok' | 'fallback' | 'skipped'
export type Side = 'left' | 'right' | 'back' | 'front' | 'any'
export type Level = 'bottom' | 'top' | 'any'
export type Effort = 'low' | 'medium' | 'high'

export const JOB_NAMES: JobName[] = ['extract', 'strategy', 'check', 'notes']

export type JobChoice = { model: string; reasoning_effort: Effort | null }
/** What the UI keeps and POSTs: all four jobs. */
export type JobChoices = Record<JobName, JobChoice>

export type RunRequestV2 = {
  items: InputItem[]
  boxes: InputBox[]
  instructions: string
  jobs?: Partial<Record<JobName, JobChoice>>
}

export type CheckResult = { name: string; passed: boolean; detail: string }
export type Tokens = { prompt: number; completion: number; reasoning: number }

export type Feedback = {
  rating: 'up' | 'down' | null // null = cleared / comment only
  comment: string // "" when none
  wrong_verdicts: number[] // check job only; [] otherwise
  updated_at: string
}
export type FeedbackIn = {
  rating?: 'up' | 'down' | null
  comment?: string // <= 2000
  wrong_verdicts?: number[] // only for the check job
}

export type JobView = {
  job: JobName
  status: JobStatus
  model: string | null
  reasoning_effort: string | null
  prompt_version: string
  calls: number
  checks: CheckResult[]
  output: unknown
  answer: unknown
  error: string | null
  cost: number
  latency_s: number
  tokens: Tokens
  feedback: Feedback | null
}

export type PlanSideV2 = {
  ok: boolean
  plan: Plan | null
  box_count: number
  // list[float] in the contract: the same 4 numbers as v1's Rank tuple
  rank: number[] | null
  errors: string[]
  hints: string[] // always [] in v2
}

export type PreferenceRow = {
  id: number
  name: string
  side: Side
  level: Level
  honoured: number
  total: number
  detail: string
}

export type VerdictValue = 'honoured' | 'not_honoured' | 'cannot_tell'
export type Verdict = {
  index: number
  instruction: string
  verdict: VerdictValue
  reason: string
}

export type BoxNotes = { box: number; steps: string[] }

export type FinalV2 = {
  source: 'model' | 'packer'
  reason: string
  plan: Plan // plan.notes = cut notes only
  notes: BoxNotes[]
  cut_notes: string[]
}

export type Totals = {
  cost: number
  seconds: number
  over_budget: boolean
  budget: { usd: number; seconds: number }
}

export type SourceV2 = 'model' | 'packer' | 'none'

// events: every event carries id and t (seconds since start)
export type RunEventV2 =
  | { id: number; t: number; type: 'started' }
  | {
      id: number
      t: number
      type: 'job'
      job: JobName
      status: JobStatus
      passed: number
      total: number
    }
  | { id: number; t: number; type: 'packer'; ok: boolean; boxes: number }
  | { id: number; t: number; type: 'chosen'; source: SourceV2; reason: string }
  | { id: number; t: number; type: 'end'; status: RunStatus; error: string | null }

export const EVENT_TYPES_V2: RunEventV2['type'][] = ['started', 'job', 'packer', 'chosen', 'end']

export type RunV2 = {
  run_id: string
  status: RunStatus
  error: string | null
  created_at: string
  finished_at: string | null
  fake: boolean
  request: RunRequestV2
  events: RunEventV2[]
  settings: PackingSettings | null
  prepared_items: PreparedItem[] | null
  prepared_boxes: PreparedBox[] | null
  jobs: JobView[] // 4 rows once terminal; [] while running (and for rare finish fallbacks)
  packer: PlanSideV2 | null
  model: PlanSideV2 | null
  source: SourceV2 | null // authoritative winner; null while running
  reason: string | null
  final: FinalV2 | null
  preferences_model: PreferenceRow[]
  preferences_packer: PreferenceRow[]
  verdicts: Verdict[]
  comparison: MetricRow[] | null
  totals: Totals | null
}

export type RunSummaryV2 = {
  run_id: string
  created_at: string
  finished_at: string | null
  status: RunStatus
  title: string
  source: SourceV2 | null
  reason: string | null
  total_cost: number | null
  total_s: number | null
  over_budget: boolean
  fake: boolean
}

export type EvalGroup = {
  job: JobName
  prompt_version: string
  model: string | null
  reasoning_effort: string | null
  calls: number // non-skipped calls
  skipped: number
  ok_rate: number | null
  fallback_rate: number | null
  all_checks_rate: number | null
  per_check: Record<string, number>
  thumbs_up: number
  thumbs_down: number
  wrong_verdict_rate: number | null // check only
  model_won_rate: number | null // strategy only, run-level
  avg_cost: number | null
  p50_latency_s: number | null
  p95_latency_s: number | null
  first_seen: string
  last_seen: string
}

export type ModelOptionV2 = {
  id: string
  label: string
  efforts: Effort[] | null // null for non-reasoning / fixed-kwargs models
  price_in: number
  price_out: number
}
export type JobConfigView = { default: JobChoice; models: ModelOptionV2[] }
export type ConfigV2 = {
  jobs: Record<JobName, JobConfigView>
  budget: { usd: number; seconds: number }
  caps: { max_item_rows: 20; max_box_rows: 10; max_units: 150; max_instruction_chars: 2000 }
  volume_margin: number
}

export type SampleV2 = Omit<Sample, 'key'> & {
  key: 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G' | 'H'
}
