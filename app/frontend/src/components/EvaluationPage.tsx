import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { JOB_NAMES, type EvalGroup, type JobName } from '../api/typesV2'
import { getEvalSummary } from '../api/v2'

const TITLE: Record<JobName, string> = {
  extract: 'Extract',
  strategy: 'Strategy',
  check: 'Check',
  notes: 'Notes',
}

const pct = (v: number | null) => (v == null ? '—' : `${Math.round(v * 1000) / 10}%`)
const usd = (v: number | null) => (v == null ? '—' : `$${v.toFixed(4)}`)
const sec = (v: number | null) => (v == null ? '—' : `${v.toFixed(1)} s`)

const th = 'pr-3 text-left font-medium'
const td = 'pr-3 tabular-nums'

function JobTable({ job, groups }: { job: JobName; groups: EvalGroup[] }) {
  return (
    <section aria-labelledby={`eval-${job}-heading`} className="space-y-1">
      <h2 id={`eval-${job}-heading`} className="text-base font-semibold">
        {TITLE[job]}
      </h2>
      <div className="overflow-x-auto">
        <table className="text-sm">
          <caption className="sr-only">
            {TITLE[job]} job: one row per prompt version, model and effort
          </caption>
          <thead className="text-xs text-slate-600">
            <tr>
              <th scope="col" className={th}>Prompt version</th>
              <th scope="col" className={th}>Model</th>
              <th scope="col" className={th}>Effort</th>
              <th scope="col" className={th}>Calls</th>
              <th scope="col" className={th}>Skipped</th>
              <th scope="col" className={th}>OK %</th>
              <th scope="col" className={th}>Fallback %</th>
              <th scope="col" className={th}>All checks pass %</th>
              <th scope="col" className={th}>Good</th>
              <th scope="col" className={th}>Bad</th>
              {job === 'check' && (
                <th scope="col" className={th}>Verdicts marked wrong %</th>
              )}
              {job === 'strategy' && (
                <th scope="col" className={th}>Model won % (run-level)</th>
              )}
              <th scope="col" className={th}>Avg cost</th>
              <th scope="col" className={th}>p50 latency</th>
              <th scope="col" className={th}>p95 latency</th>
              <th scope="col" className={th}>First seen</th>
              <th scope="col" className={th}>Last seen</th>
              <th scope="col" className={th}>Per-check pass %</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => {
              const checks = Object.entries(g.per_check)
              return (
                <tr key={`${g.prompt_version}|${g.model}|${g.reasoning_effort}`}>
                  <th scope="row" className="pr-3 text-left font-normal">
                    {g.prompt_version}
                  </th>
                  <td className="pr-3">{g.model ?? '—'}</td>
                  <td className="pr-3">{g.reasoning_effort ?? '—'}</td>
                  <td className={td}>{g.calls}</td>
                  <td className={td}>{g.skipped}</td>
                  <td className={td}>{pct(g.ok_rate)}</td>
                  <td className={td}>{pct(g.fallback_rate)}</td>
                  <td className={td}>{pct(g.all_checks_rate)}</td>
                  <td className={td}>{g.thumbs_up}</td>
                  <td className={td}>{g.thumbs_down}</td>
                  {job === 'check' && <td className={td}>{pct(g.wrong_verdict_rate)}</td>}
                  {job === 'strategy' && <td className={td}>{pct(g.model_won_rate)}</td>}
                  <td className={td}>{usd(g.avg_cost)}</td>
                  <td className={td}>{sec(g.p50_latency_s)}</td>
                  <td className={td}>{sec(g.p95_latency_s)}</td>
                  <td className="pr-3">{new Date(g.first_seen).toLocaleString()}</td>
                  <td className="pr-3">{new Date(g.last_seen).toLocaleString()}</td>
                  <td className="pr-3">
                    {checks.length === 0 ? (
                      '—'
                    ) : (
                      <details>
                        <summary className="cursor-pointer">
                          {checks.length} {checks.length === 1 ? 'check' : 'checks'}
                        </summary>
                        <ul>
                          {checks.map(([name, rate]) => (
                            <li key={name}>
                              {name}: {pct(rate)}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function EvaluationPage() {
  const [job, setJob] = useState<JobName | ''>('')
  const [includeFake, setIncludeFake] = useState(false)
  const [groups, setGroups] = useState<EvalGroup[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const token = useRef(0)

  const load = useCallback(async () => {
    const mine = ++token.current
    setLoading(true)
    setError(null)
    try {
      const r = await getEvalSummary({ job: job || undefined, includeFake })
      if (mine !== token.current) return
      setGroups(r.groups)
    } catch (e) {
      if (mine !== token.current) return
      setError(
        e instanceof ApiError ? e.messages.join('; ') : e instanceof Error ? e.message : String(e),
      )
    } finally {
      if (mine === token.current) setLoading(false)
    }
  }, [job, includeFake])

  useEffect(() => {
    void load()
  }, [load])

  const jobs = JOB_NAMES.filter((j) => groups?.some((g) => g.job === j))

  return (
    <div className="space-y-4 p-4">
      <h2 className="text-lg font-semibold">Evaluation</h2>
      <div className="flex flex-wrap items-center gap-4">
        <label className="text-sm">
          Job{' '}
          <select
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={job}
            onChange={(e) => setJob(e.target.value as JobName | '')}
          >
            <option value="">All jobs</option>
            {JOB_NAMES.map((j) => (
              <option key={j} value={j}>
                {TITLE[j]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={includeFake}
            onChange={(e) => setIncludeFake(e.target.checked)}
          />
          Include fake runs
        </label>
        <button
          type="button"
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50"
          onClick={() => void load()}
        >
          Refresh
        </button>
      </div>
      {loading && (
        <p role="status" className="text-sm text-slate-600">
          Loading evaluation…
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-800">
          Could not load the evaluation: {error}
        </p>
      )}
      {!loading && !error && groups && groups.length === 0 && (
        <p className="text-sm text-slate-600">No v2 runs yet</p>
      )}
      {groups &&
        jobs.map((j) => (
          <JobTable key={j} job={j} groups={groups.filter((g) => g.job === j)} />
        ))}
    </div>
  )
}
