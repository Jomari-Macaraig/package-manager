import type { ReactNode } from 'react'
import type { RunSummary } from '../api/types'

type HistoryRun = { run_id: string; created_at: string; title: string; status: string }

type Props<T extends HistoryRun> = {
  runs: T[]
  loading: boolean
  error: string | null
  selectedId: string | null
  onOpen: (runId: string) => void
  onRefresh: () => void
  /** replaces the model and status lines; default: v1's */
  renderMeta?: (run: T) => ReactNode
}

function v1Meta(run: RunSummary) {
  return (
    <>
      <div className="text-xs text-slate-600">
        {run.extraction_model} → {run.planning_model}
      </div>
      <div className="text-xs">
        {run.status} · {run.source ? `chosen: ${run.source}` : 'no plan chosen'} ·{' '}
        {run.total_cost == null ? 'cost —' : `$${run.total_cost.toFixed(4)}`}
      </div>
    </>
  )
}

export function HistoryList<T extends HistoryRun = RunSummary>({
  runs,
  loading,
  error,
  selectedId,
  onOpen,
  onRefresh,
  renderMeta,
}: Props<T>) {
  return (
    <section aria-labelledby="history-heading" className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 id="history-heading" className="text-base font-semibold">
          History
        </h2>
        <button
          type="button"
          className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50"
          onClick={onRefresh}
        >
          Refresh
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-800">
          {error}
        </p>
      )}
      {loading && runs.length === 0 && <p className="text-sm text-slate-600">Loading…</p>}
      {!loading && !error && runs.length === 0 && (
        <p className="text-sm text-slate-600">No runs yet.</p>
      )}
      <ul className="space-y-1">
        {runs.map((r) => (
          <li key={r.run_id}>
            <button
              type="button"
              aria-current={r.run_id === selectedId ? 'true' : undefined}
              className={
                'w-full rounded border p-2 text-left text-sm hover:bg-slate-50 ' +
                (r.run_id === selectedId ? 'border-blue-600 bg-blue-50' : 'border-slate-200')
              }
              onClick={() => onOpen(r.run_id)}
            >
              <div className="font-medium">{r.title}</div>
              <div className="text-xs text-slate-600">
                {new Date(r.created_at).toLocaleString()}
              </div>
              {renderMeta ? renderMeta(r) : v1Meta(r as unknown as RunSummary)}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
