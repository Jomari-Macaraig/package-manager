import type { Config } from '../api/types'
import { emptyItemRow, type ItemField, type ItemRow, type Validation } from '../lib/editor'

type Props = {
  rows: ItemRow[]
  onChange: (rows: ItemRow[]) => void
  caps: Config['caps']
  validation: Validation
  disabled?: boolean
}

const input =
  'w-full rounded border border-slate-300 px-1.5 py-1 text-sm aria-[invalid=true]:border-red-500 aria-[invalid=true]:bg-red-50'

export function colorSupported(v: string): boolean {
  const value = v.trim()
  if (!value) return false
  return typeof CSS !== 'undefined' && typeof CSS.supports === 'function'
    ? CSS.supports('color', value)
    : false
}

const NUM_COLS: { field: ItemField; label: string; unit?: string }[] = [
  { field: 'weight', label: 'Weight', unit: 'lb' },
  { field: 'x', label: 'X', unit: 'in' },
  { field: 'y', label: 'Y', unit: 'in' },
  { field: 'z', label: 'Z', unit: 'in' },
  { field: 'quantity', label: 'Qty' },
]

export function ItemsTable({ rows, onChange, caps, validation, disabled }: Props) {
  const update = (key: number, field: ItemField, value: string) =>
    onChange(rows.map((r) => (r.key === key ? { ...r, [field]: value } : r)))
  const err = (key: number, field: string) => validation.cells[`${key}.${field}`]

  return (
    <section aria-labelledby="items-heading" className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h2 id="items-heading" className="text-base font-semibold">
          Items
        </h2>
        <p
          className={
            'text-sm ' + (validation.units > caps.max_units ? 'text-red-700' : 'text-slate-600')
          }
          data-testid="unit-total"
        >
          {validation.units} / {caps.max_units} units
        </p>
      </div>
      <p id="color-help" className="text-xs text-slate-500">
        CSS name or hex; unrecognized colors are auto-assigned
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <caption className="sr-only">Items to pack</caption>
          <thead>
            <tr className="text-xs text-slate-600">
              <th scope="col" className="pr-1 font-medium">Name</th>
              <th scope="col" className="pr-1 font-medium">Color</th>
              {NUM_COLS.map((c) => (
                <th key={c.field} scope="col" className="pr-1 font-medium">
                  {c.label}
                  {c.unit ? ` (${c.unit})` : ''}
                </th>
              ))}
              <th scope="col" className="font-medium">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const n = i + 1
              return (
                <tr key={r.key}>
                  <td className="pr-1 pb-1">
                    <input
                      className={input}
                      aria-label={`Item ${n} name`}
                      aria-invalid={!!err(r.key, 'name')}
                      title={err(r.key, 'name')}
                      value={r.name}
                      disabled={disabled}
                      onChange={(e) => update(r.key, 'name', e.target.value)}
                    />
                  </td>
                  <td className="pr-1 pb-1">
                    <div className="flex items-center gap-1">
                      {colorSupported(r.color) ? (
                        <span
                          aria-hidden="true"
                          className="inline-block h-4 w-4 shrink-0 rounded border border-slate-300"
                          style={{ backgroundColor: r.color.trim() }}
                        />
                      ) : (
                        <span className="w-7 shrink-0 text-xs text-slate-500">auto</span>
                      )}
                      <input
                        className={input}
                        aria-label={`Item ${n} color`}
                        aria-describedby="color-help"
                        aria-invalid={!!err(r.key, 'color')}
                        value={r.color}
                        disabled={disabled}
                        onChange={(e) => update(r.key, 'color', e.target.value)}
                      />
                    </div>
                  </td>
                  {NUM_COLS.map((c) => (
                    <td key={c.field} className="pr-1 pb-1">
                      <input
                        className={input + ' min-w-14'}
                        type="number"
                        step="any"
                        aria-label={`Item ${n} ${c.label.toLowerCase()}${c.unit ? ` (${c.unit})` : ''}`}
                        aria-invalid={!!err(r.key, c.field)}
                        title={err(r.key, c.field)}
                        value={r[c.field]}
                        disabled={disabled}
                        onChange={(e) => update(r.key, c.field, e.target.value)}
                      />
                    </td>
                  ))}
                  <td className="pb-1">
                    <button
                      type="button"
                      className="rounded px-2 py-1 text-sm text-slate-600 hover:bg-slate-100"
                      aria-label={`Remove item row ${n}`}
                      disabled={disabled || rows.length <= 1}
                      onClick={() => onChange(rows.filter((x) => x.key !== r.key))}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <button
        type="button"
        className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50 disabled:opacity-50"
        disabled={disabled || rows.length >= caps.max_item_rows}
        onClick={() => onChange([...rows, emptyItemRow()])}
      >
        Add item
      </button>
      <span className="ml-2 text-xs text-slate-500">
        {rows.length} / {caps.max_item_rows} rows
      </span>
    </section>
  )
}
