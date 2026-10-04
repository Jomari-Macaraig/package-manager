import {
  JOB_NAMES,
  type ConfigV2,
  type Effort,
  type JobChoices,
  type JobName,
} from '../api/typesV2'
import { defaultJobChoices } from '../lib/editor'

type Props = {
  config: ConfigV2
  value: JobChoices
  onChange: (v: JobChoices) => void
  disabled?: boolean
}

const LABEL: Record<JobName, string> = {
  extract: 'Extract',
  strategy: 'Strategy',
  check: 'Check',
  notes: 'Notes',
}

const select = 'rounded border border-slate-300 px-2 py-1 text-sm'

export function JobSettings({ config, value, onChange, disabled }: Props) {
  const label = (job: JobName, id: string) =>
    config.jobs[job].models.find((m) => m.id === id)?.label ?? id

  const setModel = (job: JobName, id: string) => {
    const efforts = config.jobs[job].models.find((m) => m.id === id)?.efforts ?? null
    const cur = value[job].reasoning_effort
    const effort: Effort | null = efforts
      ? cur && efforts.includes(cur)
        ? cur
        : (efforts[0] ?? null)
      : null
    onChange({ ...value, [job]: { model: id, reasoning_effort: effort } })
  }
  const setEffort = (job: JobName, effort: Effort) =>
    onChange({ ...value, [job]: { ...value[job], reasoning_effort: effort } })

  return (
    <section aria-labelledby="jobs-settings-heading" className="space-y-2">
      <h2 id="jobs-settings-heading" className="text-base font-semibold">
        Jobs
      </h2>
      <ul className="space-y-0.5 text-sm">
        {JOB_NAMES.map((job) => {
          const def = config.jobs[job].default
          const cur = value[job]
          const effort = cur.reasoning_effort ? ` (${cur.reasoning_effort})` : ''
          return (
            <li key={job}>
              <span className="font-medium">{LABEL[job]}:</span> {label(job, cur.model)}
              {effort}
              {cur.model === def.model && cur.reasoning_effort === def.reasoning_effort
                ? ' — default'
                : ` — default is ${label(job, def.model)}`}
            </li>
          )
        })}
      </ul>
      <p className="text-xs text-slate-500">
        Budget per run: ${config.budget.usd} · {config.budget.seconds} s
      </p>
      <details className="text-sm">
        <summary className="cursor-pointer">Advanced</summary>
        <div className="mt-2 space-y-2">
          {JOB_NAMES.map((job) => {
            const models = config.jobs[job].models
            const efforts = models.find((m) => m.id === value[job].model)?.efforts ?? null
            return (
              <div key={job} className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <label className="text-sm">
                  {LABEL[job]} model{' '}
                  <select
                    className={select}
                    value={value[job].model}
                    disabled={disabled}
                    onChange={(e) => setModel(job, e.target.value)}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.label}
                      </option>
                    ))}
                  </select>
                </label>
                {efforts && efforts.length > 0 && (
                  <label className="text-sm">
                    {LABEL[job]} effort{' '}
                    <select
                      className={select}
                      value={value[job].reasoning_effort ?? ''}
                      disabled={disabled}
                      onChange={(e) => setEffort(job, e.target.value as Effort)}
                    >
                      {efforts.map((x) => (
                        <option key={x} value={x}>
                          {x}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
            )
          })}
          <button
            type="button"
            className="rounded border border-slate-300 px-2 py-0.5 text-sm hover:bg-slate-50 disabled:opacity-50"
            disabled={disabled}
            onClick={() => onChange(defaultJobChoices(config))}
          >
            Reset to defaults
          </button>
        </div>
      </details>
    </section>
  )
}
