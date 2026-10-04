import type { CallStats, Run, RunStatus } from '../api/types'
import { NotesLeftovers } from './NotesLeftovers'
import { PlansSideBySide } from './PlansSideBySide'
import { PreparedItemsTable } from './PreparedItemsTable'
import { SettingsPanel } from './SettingsPanel'

const badge: Record<RunStatus, string> = {
  running: 'bg-blue-100 text-blue-900',
  succeeded: 'bg-green-100 text-green-900',
  failed: 'bg-red-100 text-red-900',
  cancelled: 'bg-amber-100 text-amber-900',
  interrupted: 'bg-amber-100 text-amber-900',
}

const money = (n: number) => `$${n.toFixed(4)}`
const secs = (n: number | null | undefined) => (n == null ? '—' : `${n.toFixed(1)} s`)

function CallRow({ label, s }: { label: string; s: CallStats | null }) {
  if (!s)
    return (
      <tr>
        <th scope="row" className="pr-3 text-left font-normal">{label}</th>
        <td colSpan={5} className="text-slate-500">not called</td>
      </tr>
    )
  return (
    <tr>
      <th scope="row" className="pr-3 text-left font-normal">{label}</th>
      <td className="pr-3">
        {s.model}
        {s.reasoning_effort ? ` (${s.reasoning_effort})` : ''}
      </td>
      <td className="pr-3">{s.prompt_tokens}</td>
      <td className="pr-3">{s.completion_tokens}</td>
      <td className="pr-3">{s.reasoning_tokens}</td>
      <td>{money(s.cost)}</td>
    </tr>
  )
}

export function RunView({ run }: { run: Run }) {
  const copy = () => {
    void navigator.clipboard?.writeText(run.run_id)
  }
  const stats = run.stats
  return (
    <div className="space-y-5">
      <section aria-labelledby="run-summary-heading" className="space-y-1">
        <h2 id="run-summary-heading" className="text-base font-semibold">
          Run
        </h2>
        <p className="text-sm">
          <span className={'rounded px-2 py-0.5 text-xs font-semibold ' + badge[run.status]}>
            Status: {run.status}
          </span>
        </p>
        <p className="text-sm">
          Run id: <code className="text-xs">{run.run_id}</code>{' '}
          <button
            type="button"
            className="rounded border border-slate-300 px-1.5 text-xs hover:bg-slate-50"
            onClick={copy}
          >
            Copy run id
          </button>
        </p>
        <p className="text-sm text-slate-600">
          Created {new Date(run.created_at).toLocaleString()}
          {run.finished_at ? ` · finished ${new Date(run.finished_at).toLocaleString()}` : ''}
        </p>
        {run.error && (
          <p role="alert" className="text-sm text-red-800">
            Error: {run.error}
          </p>
        )}
      </section>

      {stats && (
        <section aria-labelledby="stats-heading" className="space-y-1">
          <h2 id="stats-heading" className="text-base font-semibold">
            Stats
          </h2>
          <table className="text-sm">
            <caption className="sr-only">Model calls</caption>
            <thead className="text-xs text-slate-600">
              <tr>
                <th scope="col" className="pr-3 text-left font-medium">Call</th>
                <th scope="col" className="pr-3 text-left font-medium">Model</th>
                <th scope="col" className="pr-3 text-left font-medium">Prompt tokens</th>
                <th scope="col" className="pr-3 text-left font-medium">Completion tokens</th>
                <th scope="col" className="pr-3 text-left font-medium">Reasoning tokens</th>
                <th scope="col" className="text-left font-medium">Cost</th>
              </tr>
            </thead>
            <tbody>
              <CallRow label="Extraction" s={stats.extraction} />
              <CallRow label="Planning" s={stats.planning} />
            </tbody>
          </table>
          <p className="text-sm">
            Total: {stats.total_tokens} tokens · {money(stats.total_cost)}
          </p>
          <p className="text-sm text-slate-600">
            Timings: extract {secs(stats.timings.extract_s)} · packer{' '}
            {secs(stats.timings.packer_s)} · plan {secs(stats.timings.plan_s)} · total{' '}
            {secs(stats.timings.total_s)}
          </p>
        </section>
      )}

      <SettingsPanel settings={run.settings} />
      <PreparedItemsTable items={run.prepared_items} />
      <PlansSideBySide run={run} />
      <NotesLeftovers plan={run.final?.plan ?? null} />
    </div>
  )
}
