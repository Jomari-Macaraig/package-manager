import type { RunSummary } from '../api/types'

type Props = {
  runs: RunSummary[]
  loading: boolean
  error: string | null
  selectedId: string | null
  onOpen: (runId: string) => void
  onRefresh: () => void
}

export function HistoryList({ runs, loading, error, selectedId, onOpen, onRefresh }: Props) {
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
              <div className="text-xs text-slate-600">
                {r.extraction_model} → {r.planning_model}
              </div>
              <div className="text-xs">
                {r.status} · {r.source ? `chosen: ${r.source}` : 'no plan chosen'} ·{' '}
                {r.total_cost == null ? 'cost —' : `$${r.total_cost.toFixed(4)}`}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
