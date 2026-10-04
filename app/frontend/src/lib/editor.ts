import type { Config, InputBox, InputItem, PlanStart, RunRequest } from '../api/types'
import { JOB_NAMES, type ConfigV2, type JobChoices, type RunRequestV2 } from '../api/typesV2'

/** Editable rows keep every cell as a string so blank/partial input is representable. */
export type ItemRow = {
  key: number
  name: string
  color: string
  weight: string
  x: string
  y: string
  z: string
  quantity: string
}
export type BoxRow = {
  key: number
  name: string
  weightMax: string
  x: string
  y: string
  z: string
  stock: string
}

export type ItemField = Exclude<keyof ItemRow, 'key'>
export type BoxField = Exclude<keyof BoxRow, 'key'>

let nextKey = 1
export const newKey = () => nextKey++

export const emptyItemRow = (): ItemRow => ({
  key: newKey(),
  name: '',
  color: '',
  weight: '',
  x: '',
  y: '',
  z: '',
  quantity: '1',
})
export const emptyBoxRow = (): BoxRow => ({
  key: newKey(),
  name: '',
  weightMax: '',
  x: '',
  y: '',
  z: '',
  stock: '',
})

export const itemRowsFromInput = (items: InputItem[]): ItemRow[] =>
  items.map((i) => ({
    key: newKey(),
    name: i.name,
    color: i.color ?? '',
    weight: String(i.weight),
    x: String(i.x),
    y: String(i.y),
    z: String(i.z),
    quantity: String(i.quantity),
  }))

export const boxRowsFromInput = (boxes: InputBox[]): BoxRow[] =>
  boxes.map((b) => ({
    key: newKey(),
    name: b.name,
    weightMax: String(b.weightMax),
    x: String(b.x),
    y: String(b.y),
    z: String(b.z),
    stock: b.stock == null ? '' : String(b.stock),
  }))

export const parseNum = (s: string): number =>
  s.trim() === '' ? NaN : Number(s)

export type Validation = {
  /** error text per cell, keyed `${row.key}.${field}` */
  cells: Record<string, string>
  /** human-readable list of every problem */
  messages: string[]
  units: number
  valid: boolean
}

// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/

export function validateEditor(
  items: ItemRow[],
  boxes: BoxRow[],
  instructions: string,
  caps: Config['caps'],
): Validation {
  const cells: Record<string, string> = {}
  const messages: string[] = []
  const add = (key: number, field: string, label: string, msg: string) => {
    cells[`${key}.${field}`] = msg
    messages.push(`${label}: ${msg}`)
  }

  if (items.length < 1) messages.push('Add at least one item row')
  if (items.length > caps.max_item_rows)
    messages.push(`At most ${caps.max_item_rows} item rows`)
  if (boxes.length < 1) messages.push('Add at least one box row')
  if (boxes.length > caps.max_box_rows)
    messages.push(`At most ${caps.max_box_rows} box rows`)

  const checkName = (
    rows: { key: number; name: string }[],
    kind: string,
  ) => {
    const seen = new Map<string, number>()
    rows.forEach((r, i) => {
      const label = `${kind} ${i + 1}`
      const n = r.name.trim()
      if (!n) return add(r.key, 'name', label, 'name is required')
      if (n.length > 60) add(r.key, 'name', label, 'name is longer than 60 characters')
      else if (CONTROL.test(n)) add(r.key, 'name', label, 'name has control characters')
      const lower = n.toLowerCase()
      if (seen.has(lower)) add(r.key, 'name', label, 'name must be unique')
      else seen.set(lower, r.key)
    })
  }
  checkName(items, 'Item')
  checkName(boxes, 'Box')

  const num = (
    key: number,
    field: string,
    label: string,
    raw: string,
    opts: { min: number; minExclusive?: boolean; max: number; integer?: boolean },
  ) => {
    const v = parseNum(raw)
    if (!Number.isFinite(v)) return add(key, field, label, `${field} must be a number`)
    if (opts.integer && !Number.isInteger(v))
      return add(key, field, label, `${field} must be a whole number`)
    if (opts.minExclusive ? v <= opts.min : v < opts.min)
      return add(
        key,
        field,
        label,
        `${field} must be ${opts.minExclusive ? 'greater than' : 'at least'} ${opts.min}`,
      )
    if (v > opts.max) add(key, field, label, `${field} must be at most ${opts.max}`)
  }

  let units = 0
  items.forEach((r, i) => {
    const label = `Item ${i + 1}`
    if (r.color.length > 32) add(r.key, 'color', label, 'color is longer than 32 characters')
    else if (CONTROL.test(r.color)) add(r.key, 'color', label, 'color has control characters')
    num(r.key, 'weight', label, r.weight, { min: 0, max: 10000 })
    for (const f of ['x', 'y', 'z'] as const)
      num(r.key, f, label, r[f], { min: 0, minExclusive: true, max: 1000 })
    num(r.key, 'quantity', label, r.quantity, { min: 1, max: 1e9, integer: true })
    const q = parseNum(r.quantity)
    if (Number.isInteger(q) && q >= 1) units += q
  })
  if (units > caps.max_units)
    messages.push(`Total quantity ${units} is over the limit of ${caps.max_units}`)

  boxes.forEach((r, i) => {
    const label = `Box ${i + 1}`
    num(r.key, 'weightMax', label, r.weightMax, { min: 0, minExclusive: true, max: 10000 })
    for (const f of ['x', 'y', 'z'] as const)
      num(r.key, f, label, r[f], { min: 0, minExclusive: true, max: 1000 })
    if (r.stock.trim() !== '')
      num(r.key, 'stock', label, r.stock, { min: 0, max: 1000, integer: true })
  })

  if (instructions.length > caps.max_instruction_chars)
    messages.push(`Instructions are over ${caps.max_instruction_chars} characters`)
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u0008\u000b-\u001f\u007f]/.test(instructions))
    messages.push('Instructions contain control characters')

  return { cells, messages, units, valid: messages.length === 0 }
}

export type ModelChoice = {
  extractionModel: string
  planningModel: string
  effort: 'low' | 'medium' | 'high'
  start: PlanStart
}

/** The v2 job choices a fresh page starts with: every job's default. */
export function defaultJobChoices(config: ConfigV2): JobChoices {
  const out = {} as JobChoices
  for (const j of JOB_NAMES) out[j] = { ...config.jobs[j].default }
  return out
}

/** Build the v2 POST body; all four jobs are sent. */
export function toRequestV2(
  items: ItemRow[],
  boxes: BoxRow[],
  instructions: string,
  jobs: JobChoices,
): RunRequestV2 {
  const base = toRequest(items, boxes, instructions, {
    extractionModel: '',
    planningModel: '',
    effort: 'low',
    start: 'improve',
  })
  const choices = {} as JobChoices
  for (const j of JOB_NAMES) choices[j] = { ...jobs[j] }
  return { items: base.items, boxes: base.boxes, instructions, jobs: choices }
}

/** Build the POST body. No ids are sent; blank stock becomes null. */
export function toRequest(
  items: ItemRow[],
  boxes: BoxRow[],
  instructions: string,
  models: ModelChoice,
): RunRequest {
  return {
    items: items.map((r) => ({
      name: r.name.trim(),
      color: r.color.trim() === '' ? null : r.color.trim(),
      weight: parseNum(r.weight),
      x: parseNum(r.x),
      y: parseNum(r.y),
      z: parseNum(r.z),
      quantity: parseNum(r.quantity),
    })),
    boxes: boxes.map((r) => ({
      name: r.name.trim(),
      weightMax: parseNum(r.weightMax),
      x: parseNum(r.x),
      y: parseNum(r.y),
      z: parseNum(r.z),
      stock: r.stock.trim() === '' ? null : parseNum(r.stock),
    })),
    instructions,
    extraction: { model: models.extractionModel },
    planning: {
      model: models.planningModel,
      reasoning_effort: models.effort,
      start: models.start,
    },
  }
}
