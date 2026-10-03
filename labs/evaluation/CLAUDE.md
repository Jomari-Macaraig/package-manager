# labs/evaluation

Compares our planner with the reference engine (Paccurate) on box count and
box types. Kept deliberately simple: this is a simple packaging manager.

- `<case>/input.json` — the engine request; `<case>/output.json` — its response.
- `evaluate.py` — `python evaluate.py` parses cases and prints the reference;
  `--model gpt-5.4-mini [--effort low] [cases...]` also runs our planner
  (`planner.plan_with_validation`) and writes `results-<model>.json` plus
  `results-<model>.md`. Both modes print a Markdown comparison table: reference
  vs ours per case (boxes, box types, leftovers, same types?, verdict).

Parsing: engine X is height like ours (schema: "the 'up' direction is X"), so
dimensions map directly. Engine `weightMax` is gross, so contents limit =
`weightMax - weightTare`. A response box named `leftovers` is the engine's
overflow bucket: its units count as leftovers, not a box. Cases
using `boxTypeGenerators` (engine-generated box sizes) or `boxTypeSets`
(carrier catalogs) are **skipped**: the box list isn't in the input, so
there's no fair comparison. Also skipped, to keep this a simple packaging
manager: cases with engine rules on their items (e.g. `compact-pattern`
nesting, `lock-orientation`) and `lowest-cost` cases whose boxes carry a
`rateTable`/`price` (box choice by shipping cost, not size). Only rules about items in the case are noted. If the output's packed
units don't match the input's quantities, the case is flagged
`OUTPUT DOES NOT MATCH INPUT` (wrong output file); a case whose input equals
an earlier case's is flagged `DUPLICATE`.

Verdict per case: fewer leftover units wins, then fewer boxes, then less total
box volume (`better` / `same` / `worse`; `invalid` if our plan fails
`validate_plan`). Engine-only options (rules, cohortPacking, usableSpace, rate
tables, lowest-cost goal) are printed as notes, not modelled.
