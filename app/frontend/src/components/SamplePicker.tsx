import { useState } from 'react'
import type { Sample } from '../api/types'

type Props = {
  samples: Sample[]
  /** true when the user changed the tables/instructions since the last load */
  edited: boolean
  onLoad: (s: Sample) => void
  disabled?: boolean
}

export function SamplePicker({ samples, edited, onLoad, disabled }: Props) {
  const [key, setKey] = useState('')
  const selected = samples.find((s) => s.key === key)

  const load = () => {
    if (!selected) return
    if (edited && !window.confirm('Replace your edits with this sample?')) return
    onLoad(selected)
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label htmlFor="sample-select" className="text-sm font-medium">
        Sample order
      </label>
      <select
        id="sample-select"
        className="rounded border border-slate-300 px-2 py-1 text-sm"
        value={key}
        disabled={disabled}
        onChange={(e) => setKey(e.target.value)}
      >
        <option value="">Choose…</option>
        {samples.map((s) => (
          <option key={s.key} value={s.key}>
            Sample {s.key}: {s.title}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50 disabled:opacity-50"
        disabled={disabled || !selected}
        onClick={load}
      >
        Load
      </button>
      {selected && <span className="text-xs text-slate-500">{selected.description}</span>}
    </div>
  )
}
