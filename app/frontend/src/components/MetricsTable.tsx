import type { MetricRow } from '../api/types'

const verdictStyle = {
  better: 'text-green-800',
  worse: 'text-red-800',
  same: 'text-slate-600',
} as const

const num = (v: number | null, unit: string) =>
  v == null ? '—' : `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ''}`

const delta = (v: number | null, unit: string) =>
  v == null ? '' : `${v > 0 ? '+' : ''}${num(v, unit)}`

/** Model vs code packer, one row per metric (computed by the backend). */
export function MetricsTable({ rows }: { rows: MetricRow[] | null | undefined }) {
  if (!rows || rows.length === 0) return null
  const count = (v: string) => rows.filter((r) => r.verdict === v).length
  const compared = rows.some((r) => r.verdict)
  return (
    <section aria-labelledby="metrics-heading" className="space-y-1">
      <h2 id="metrics-heading" className="text-base font-semibold">
        Metrics
      </h2>
      <p className="text-sm">
        {compared
          ? `Model vs packer: ${count('better')} better · ${count('worse')} worse · ${count('same')} same`
          : 'Only one side has a valid plan; nothing to compare.'}
      </p>
      <table className="text-sm">
        <caption className="sr-only">Plan metrics, model vs code packer</caption>
        <thead className="text-xs text-slate-600">
          <tr>
            <th scope="col" className="pr-4 text-left font-medium">Metric</th>
            <th scope="col" className="pr-4 text-right font-medium">Model</th>
            <th scope="col" className="pr-4 text-right font-medium">Code packer</th>
            <th scope="col" className="pr-4 text-right font-medium">Δ model</th>
            <th scope="col" className="text-left font-medium">Model is</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.metric}>
              <th scope="row" className="pr-4 text-left font-normal">
                {r.label}
                {r.direction !== 'info' && (
                  <span className="text-xs text-slate-500"> ({r.direction} is better)</span>
                )}
              </th>
              <td className="pr-4 text-right tabular-nums">{num(r.model, r.unit)}</td>
              <td className="pr-4 text-right tabular-nums">{num(r.packer, r.unit)}</td>
              <td className="pr-4 text-right tabular-nums">{delta(r.delta, r.unit)}</td>
              <td className={r.verdict ? verdictStyle[r.verdict] : ''}>{r.verdict ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
