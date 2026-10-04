import type { Verdict } from '../api/typesV2'

const WORD: Record<Verdict['verdict'], string> = {
  honoured: 'Honoured',
  not_honoured: 'Not honoured',
  cannot_tell: 'Cannot tell',
}

type Props = {
  verdicts: Verdict[]
  /** verdict indices marked wrong */
  wrong: number[]
  onToggle: (index: number) => void
  /** marking is only accepted once the run is terminal */
  disabled?: boolean
}

/** The checker's verdicts. "Mark wrong" feeds the check job's feedback. */
export function VerdictsList({ verdicts, wrong, onToggle, disabled }: Props) {
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-medium">Verdicts</h3>
      {verdicts.length === 0 ? (
        <p className="text-sm text-slate-600">No verdicts (the check was skipped).</p>
      ) : (
        <ul className="space-y-1.5 text-sm">
          {verdicts.map((v) => {
            const marked = wrong.includes(v.index)
            return (
              <li key={v.index} className="rounded border border-slate-200 p-2">
                <p>
                  <span className="font-medium">{WORD[v.verdict]}</span>: {v.instruction}
                </p>
                <p className="text-xs text-slate-600">{v.reason}</p>
                <button
                  type="button"
                  aria-pressed={marked}
                  aria-label={`Mark wrong: ${v.instruction}`}
                  disabled={disabled}
                  className={
                    'mt-1 rounded border px-2 py-0.5 text-xs disabled:opacity-50 ' +
                    (marked ? 'border-red-700 bg-red-100 font-semibold' : 'border-slate-300 hover:bg-slate-50')
                  }
                  onClick={() => onToggle(v.index)}
                >
                  {marked ? 'Marked wrong' : 'Mark wrong'}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
