import type { Config } from '../api/types'
import { emptyBoxRow, type BoxField, type BoxRow, type Validation } from '../lib/editor'

type Props = {
  rows: BoxRow[]
  onChange: (rows: BoxRow[]) => void
  caps: Config['caps']
  validation: Validation
  disabled?: boolean
}

const input =
  'w-full rounded border border-slate-300 px-1.5 py-1 text-sm aria-[invalid=true]:border-red-500 aria-[invalid=true]:bg-red-50'

const NUM_COLS: { field: BoxField; label: string; unit?: string }[] = [
  { field: 'weightMax', label: 'Max weight', unit: 'lb' },
  { field: 'x', label: 'X', unit: 'in' },
  { field: 'y', label: 'Y', unit: 'in' },
  { field: 'z', label: 'Z', unit: 'in' },
  { field: 'stock', label: 'Stock' },
]

export function BoxesTable({ rows, onChange, caps, validation, disabled }: Props) {
  const update = (key: number, field: BoxField, value: string) =>
    onChange(rows.map((r) => (r.key === key ? { ...r, [field]: value } : r)))
  const err = (key: number, field: string) => validation.cells[`${key}.${field}`]

  return (
    <section aria-labelledby="boxes-heading" className="space-y-2">
      <h2 id="boxes-heading" className="text-base font-semibold">
        Boxes
      </h2>
      <p className="text-xs text-slate-500">Leave stock blank for unlimited.</p>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <caption className="sr-only">Box types available</caption>
          <thead>
            <tr className="text-xs text-slate-600">
              <th scope="col" className="pr-1 font-medium">Name</th>
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
                      aria-label={`Box ${n} name`}
                      aria-invalid={!!err(r.key, 'name')}
                      title={err(r.key, 'name')}
                      value={r.name}
                      disabled={disabled}
                      onChange={(e) => update(r.key, 'name', e.target.value)}
                    />
                  </td>
                  {NUM_COLS.map((c) => (
                    <td key={c.field} className="pr-1 pb-1">
                      <input
                        className={input + ' min-w-14'}
                        type="number"
                        step="any"
                        aria-label={`Box ${n} ${c.label.toLowerCase()}${c.unit ? ` (${c.unit})` : ''}`}
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
                      aria-label={`Remove box row ${n}`}
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
        disabled={disabled || rows.length >= caps.max_box_rows}
        onClick={() => onChange([...rows, emptyBoxRow()])}
      >
        Add box
      </button>
      <span className="ml-2 text-xs text-slate-500">
        {rows.length} / {caps.max_box_rows} rows
      </span>
    </section>
  )
}
