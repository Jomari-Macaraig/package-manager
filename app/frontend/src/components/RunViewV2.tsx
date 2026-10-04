import { useState } from 'react'
import { isTerminal } from '../api/client'
import type { RunStatus } from '../api/types'
import type { Feedback, JobName, PlanSideV2, RunEventV2, RunV2 } from '../api/typesV2'
import { renderUrlV2, svgUrlV2, type PlanKeyV2 } from '../api/v2'
import { JobsPanel } from './JobsPanel'
import { MetricsTable } from './MetricsTable'
import { NotesSteps } from './NotesSteps'
import { PlanPanel } from './PlanPanel'
import { PreferencesTable } from './PreferencesTable'
import { SettingsPanel } from './SettingsPanel'
import { VerdictsList } from './VerdictsList'

const badge: Record<RunStatus, string> = {
  running: 'bg-blue-100 text-blue-900',
  succeeded: 'bg-green-100 text-green-900',
  failed: 'bg-red-100 text-red-900',
  cancelled: 'bg-amber-100 text-amber-900',
  interrupted: 'bg-amber-100 text-amber-900',
}

const money = (n: number) => `$${n.toFixed(4)}`

type Props = {
  run: RunV2
  /** live events while the run is going; defaults to the run's own */
  events?: RunEventV2[]
}

/** Callers key this by run id, so local feedback state resets for another run. */
export function RunViewV2({ run, events }: Props) {
  const terminal = isTerminal(run.status)
  const [saved, setSaved] = useState<Partial<Record<JobName, Feedback>>>({})
  const [wrong, setWrong] = useState<number[]>(
    () => run.jobs.find((j) => j.job === 'check')?.feedback?.wrong_verdicts ?? [],
  )

  const jobs = run.jobs.map((j) => (saved[j.job] ? { ...j, feedback: saved[j.job]! } : j))
  const toggleWrong = (i: number) =>
    setWrong((cur) => (cur.includes(i) ? cur.filter((x) => x !== i) : [...cur, i].sort((a, b) => a - b)))

  const copy = () => {
    void navigator.clipboard?.writeText(run.run_id)
  }
  const totals = run.totals
  const final = run.final
  const noPlan = terminal && !final

  const urls = (key: PlanKeyV2) => ({
    renderSrc: (box: number, upto?: number) => renderUrlV2(run.run_id, key, box, upto),
    downloadHref: svgUrlV2(run.run_id, key),
  })
  const sidePanel = (key: 'model' | 'packer', title: string, side: PlanSideV2 | null) => (
    <PlanPanel
      key={`${run.run_id}-${key}`}
      title={title}
      planKey={key}
      side={side}
      runId={run.run_id}
      terminal={terminal}
      chosen={final?.source === key}
      {...urls(key)}
    />
  )

  return (
    <div className="space-y-5">
      <section aria-labelledby="run-summary-heading" className="space-y-1">
        <h2 id="run-summary-heading" className="text-base font-semibold">
          Run
        </h2>
        <p className="text-sm">
          <span className={'rounded px-2 py-0.5 text-xs font-semibold ' + badge[run.status]}>
            Status: {run.status}
          </span>{' '}
          {run.fake && (
            <span className="rounded bg-slate-200 px-2 py-0.5 text-xs font-semibold text-slate-800">
              Fake LLM run
            </span>
          )}
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
        {totals && (
          <p className="text-sm">
            Cost {money(totals.cost)} of {money(totals.budget.usd)} · {totals.seconds.toFixed(1)} s of{' '}
            {totals.budget.seconds} s{' '}
            {totals.over_budget && (
              <span className="rounded bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-900">
                Over budget
              </span>
            )}
          </p>
        )}
        {run.source && (
          <p className="text-sm">
            {run.source === 'none' ? 'Winner: none' : `Winner: ${run.source}`}
            {run.reason ? ` — ${run.reason}` : ''}
          </p>
        )}
        {run.error && (
          <p role="alert" className="text-sm text-red-800">
            Error: {run.error}
          </p>
        )}
      </section>

      <section aria-labelledby="final-heading" className="space-y-2">
        <h2 id="final-heading" className="text-base font-semibold">
          Final plan
        </h2>
        {final ? (
          <>
            <PlanPanel
              key={`${run.run_id}-final`}
              title="Final plan"
              planKey="final"
              side={{
                ok: true,
                plan: final.plan,
                box_count: final.plan.boxes.length,
                rank: null,
                errors: [],
                hints: [],
              }}
              runId={run.run_id}
              terminal={terminal}
              chosen={false}
              label="final plan"
              {...urls('final')}
            />
            <NotesSteps
              notes={final.notes}
              cutNotes={final.cut_notes}
              boxNames={final.plan.boxes.map((b) => b.name)}
            />
          </>
        ) : noPlan ? (
          <p className="text-sm font-medium text-red-800">No valid plan</p>
        ) : (
          <p className="text-sm text-slate-600">The final plan appears when the run finishes.</p>
        )}
      </section>

      <JobsPanel
        runId={run.run_id}
        jobs={jobs}
        terminal={terminal}
        events={events ?? run.events}
        wrongVerdicts={wrong}
        onFeedback={(job, fb) => setSaved((cur) => ({ ...cur, [job]: fb }))}
      />

      <section aria-labelledby="plans-heading" className="space-y-2">
        <h2 id="plans-heading" className="text-base font-semibold">
          Plans
        </h2>
        <div className="grid gap-3 lg:grid-cols-2">
          {sidePanel('model', 'Model', run.model)}
          {sidePanel('packer', 'Code packer', run.packer)}
        </div>
      </section>

      <MetricsTable rows={run.comparison} />

      <section aria-labelledby="prefs-heading" className="space-y-2">
        <h2 id="prefs-heading" className="text-base font-semibold">
          Preferences and verdicts
        </h2>
        <div className="grid gap-3 lg:grid-cols-2">
          <PreferencesTable title="Model plan preferences" rows={run.preferences_model} />
          <PreferencesTable title="Code packer plan preferences" rows={run.preferences_packer} />
        </div>
        <VerdictsList
          verdicts={run.verdicts}
          wrong={wrong}
          onToggle={toggleWrong}
          disabled={!terminal}
        />
      </section>

      <SettingsPanel settings={run.settings} />
    </div>
  )
}
