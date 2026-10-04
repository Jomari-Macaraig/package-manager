// Contract types copied from docs/plans/2026-10-04-packing-playground.md ("API contract").
// Only changes: `export` keyword and the Rank comment.

export type InputItem = {
  name: string;          // 1–60 chars, trimmed, unique case-insensitive, no control chars
  color: string | null;  // ≤ 32 chars, no control chars; null/"" = auto (tab10)
  weight: number;        // ≥ 0 and ≤ 10000, lb
  x: number; y: number; z: number;  // > 0 and ≤ 1000, in
  quantity: number;      // integer ≥ 1
};
export type InputBox = {
  name: string;          // same rules as item name
  weightMax: number;     // > 0 and ≤ 10000
  x: number; y: number; z: number;  // > 0 and ≤ 1000
  stock: number | null;  // integer 0..1000; null = unlimited
};
export type RunRequest = {
  items: InputItem[];    // 1..20 rows; Σ quantity ≤ 150
  boxes: InputBox[];     // 1..10 rows
  instructions: string;  // ≤ 2000 chars, no control chars except \n \t
  extraction: { model: string };                       // must be in config
  planning: { model: string; reasoning_effort: "low" | "medium" | "high";
              start: PlanStart };  // default "improve" when omitted
};
// improve: the model edits the code packer's plan; scratch: it plans alone;
// guide: it picks box types, item order and rotations, code places the units
export type PlanStart = "improve" | "scratch" | "guide";

export type Dimensions = { x: number; y: number; z: number };
export type Item = { id: number; color: string; name: string; weight: number;
              dimensions: Dimensions; quantity: number };
export type PackedItem = Item & {           // one unit; quantity = 1
  packed_index: number;              // loading step within its box (1..n)
  placement_position: { x: number; y: number; z: number };  // min corner
};                                   // dimensions = rotated extents
export type PlanBox = { id: number;         // box TYPE id
                 name: string; dimensions: Dimensions;
                 max_contents_weight: number; items: PackedItem[] };
export type LeftoverGroup = { items: Item[]; reason: string };
export type Plan = {                        // labs schemas.Response.model_dump()
  length_unit: "in"; weight_unit: "lb";
  coordinate_system: Record<string, string>;
  boxes: PlanBox[];                  // physical box N = boxes[N-1]
  leftover_items: LeftoverGroup[];
  notes: string[];                   // cut notes (code) first, then model notes
};
// Field names verified against labs/schemas.py (Boxes, PackedItem,
// LeftoverItems, Item) on 2026-10-04.

// Rank tuple order: leftover units, boxes used, shipped volume (in3), summed fill height (in). Lower is better.
export type Rank = [leftover_units: number, boxes: number,
             shipped_volume_in3: number, summed_fill_height_in: number];

export type PackingSettings = {
  padding: number; padding_by_item: { item_id: number; padding: number }[];
  blocks: { item_id: number; size: number; arrangement: "stack" | "compact";
            grid: number[] | null }[];
  upright: number[]; fixed_orientation: number[]; no_stack_on: number[];
  max_load_on_top: { item_id: number; max_weight: number }[];
  floor_only_over_weight: number | null;
  wall_clearance: number; cut_step: number | null; min_cut_height: number | null;
  carrier_max_weight: number | null; carrier_max_length: number | null;
  carrier_max_girth: number | null; min_fill: number | null;
  max_boxes: number | null; keep_together: number[][]; keep_apart: number[][];
  balance_boxes: boolean; soft_instructions: string[];
};
// Verified against labs/settings.py PackingSettings / BlockRule /
// LoadLimit / ItemPadding on 2026-10-04.

export type PreparedItem = {
  id: number; source_id: number; name: string; color: string;
  weight: number; dimensions: Dimensions; quantity: number;
  fixed?: boolean; upright?: boolean; no_stack?: boolean;
  max_load?: number; floor_only?: boolean;
};
export type PreparedBox = { id: number; name: string; weightMax: number;
                     dimensions: Dimensions; stock?: number };

export type PlanSide = {
  ok: boolean; rank: Rank | null; errors: string[]; hints: string[];
  plan: Plan | null;          // null when invalid
  box_count: number;          // = plan?.boxes.length ?? 0
};
export type ModelSide = PlanSide & { rounds: number; improved: boolean };
// ModelSide.hints/errors are the model's own (captured before the packer
// replaces them); errors include "model call failed: …" for API errors.

export type CallStats = { model: string; reasoning_effort: string | null;
                   prompt_tokens: number; completion_tokens: number;
                   reasoning_tokens: number; cost: number };
export type RunStatus = "running" | "succeeded" | "failed" | "cancelled" | "interrupted";

export type RunEvent =
  | { id: number; type: "started"; t: number }
  | { id: number; type: "extracted"; t: number; tokens: number; skipped: boolean }
  | { id: number; type: "prepared"; t: number; item_types: number; units: number }
  | { id: number; type: "packer"; t: number; ok: boolean; boxes: number; errors_count: number }
  | { id: number; type: "round"; t: number; n: number; kind: "fix" | "improve";
      valid: boolean; errors_count: number }
  | { id: number; type: "chosen"; t: number; source: "model" | "packer" | null }
  | { id: number; type: "end"; t: number; status: RunStatus; error: string | null };
// id = 0-based index in the run's event list; t = seconds since start.

export type Run = {
  run_id: string; trace_id: string; request_id: string;
  status: RunStatus; error: string | null;
  created_at: string; finished_at: string | null;   // ISO 8601 UTC
  client_ip: string; user_agent: string;            // UA truncated to 200
  request: RunRequest;
  loop: { max_rounds: 3; improve_rounds: 1 };
  // Null while status = "running", and when the run failed before the stage:
  settings: PackingSettings | null;
  prepared_items: PreparedItem[] | null;
  prepared_boxes: PreparedBox[] | null;
  final: { source: "model" | "packer"; plan: Plan } | null;  // null = neither side ok
  model: ModelSide | null;
  packer: PlanSide | null;
  stats: { extraction: CallStats | null; planning: CallStats | null;
           total_tokens: number; total_cost: number;
           timings: { extract_s: number | null; packer_s: number | null;
                      plan_s: number | null; total_s: number | null } } | null;
  comparison?: MetricRow[] | null;  // model vs packer; null = no plans
  events: RunEvent[];   // persisted as they happen; complete after end
};

export type RunSummary = { run_id: string; created_at: string; finished_at: string | null;
  status: RunStatus; title: string;            // first 3 item names
  extraction_model: string; planning_model: string;
  source: "model" | "packer" | null; total_cost: number | null; client_ip: string };

export type ModelOption = { id: string; label: string; price_in: number; price_out: number };
export type Config = {
  extraction_models: (ModelOption & { reasoning: boolean })[];
  planning_models: (ModelOption & { efforts: ("low" | "medium" | "high")[] })[];
  defaults: { extraction_model: string; planning_model: string;
              reasoning_effort: "low" | "medium" | "high" };
  loop: { max_rounds: 3; improve_rounds: 1 };
  caps: { max_item_rows: 20; max_box_rows: 10; max_units: 150;
          max_instruction_chars: 2000 };
};
export type Sample = { key: "A" | "B" | "C" | "D" | "E"; title: string; description: string;
                items: InputItem[]; boxes: InputBox[]; instructions: string };

/** GET /runs/{id}/history: the chat messages sent to and received from the
 *  model, per stage. Empty until the stage finishes. */
export interface ChatMessage {
  role: string
  content: unknown
}
export interface RunHistory {
  extraction: ChatMessage[]
  planning: ChatMessage[]
}

/** packing.metrics.compare row; verdict is for the model vs the packer. */
export type MetricRow = {
  metric: string; label: string; unit: string;
  direction: "lower" | "higher" | "info";
  model: number | null; packer: number | null; delta: number | null;
  verdict: "better" | "worse" | "same" | null;
};
