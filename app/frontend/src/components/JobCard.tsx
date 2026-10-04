import { useState } from 'react'
import { getJobMessages } from '../api/v2'
import type { ChatMessage } from '../api/types'
import type { Feedback, JobView } from '../api/typesV2'
import { FeedbackControls } from './FeedbackControls'

const STATUS_STYLE: Record<JobView['status'], string> = {
  ok: 'bg-green-100 text-green-900',
  fallback: 'bg-amber-100 text-amber-900',
  skipped: 'bg-slate-200 text-slate-800',
}

const LABEL: Record<JobView['job'], string> = {
  extract: 'Extract',
  strategy: 'Strategy',
  check: 'Check',
  notes: 'Notes',
}

const text = (c: unknown) => (typeof c === 'string' ? c : JSON.stringify(c, null, 2))

type Props = {
  runId: string
  job: JobView
  /** feedback and the messages are offered once the run is terminal */
  terminal: boolean
  /** check job only: verdict indices marked wrong */
  wrongVerdicts?: number[]
  onFeedback: (job: JobView['job'], fb: Feedback) => void
}

export function JobCard({ runId, job, terminal, wrongVerdicts, onFeedback }: Props) {
  const [msgs, setMsgs] = useState<ChatMessage[] | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [msgError, setMsgError] = useState<string | null>(null)
  const headingId = `job-${job.job}-heading`

  const toggleMessages = async () => {
    if (open) return setOpen(false)
    setOpen(true)
    if (msgs) return
    setLoading(true)
    setMsgError(null)
    try {
      setMsgs((await getJobMessages(runId, job.job)).messages)
    } catch (e) {
      setMsgError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const skipped = job.status === 'skipped'

  return (
    <article aria-labelledby={headingId} className="space-y-2 rounded border border-slate-300 p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 id={headingId} className="font-semibold">
          {LABEL[job.job]}
        </h3>
        <span className={'rounded px-2 py-0.5 text-xs font-semibold ' + STATUS_STYLE[job.status]}>
          Status: {job.status}
        </span>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-xs text-slate-700">
        <dt>Model</dt>
        <dd>{job.model ?? '—'}</dd>
        <dt>Effort</dt>
        <dd>{job.reasoning_effort ?? '—'}</dd>
        <dt>Prompt version</dt>
        <dd>{job.prompt_version}</dd>
        <dt>Calls</dt>
        <dd>{job.calls}</dd>
        <dt>Cost</dt>
        <dd>${job.cost.toFixed(4)}</dd>
        <dt>Latency</dt>
        <dd>{job.latency_s.toFixed(1)} s</dd>
        <dt>Tokens</dt>
        <dd>
          {job.tokens.prompt} in · {job.tokens.completion} out · {job.tokens.reasoning} reasoning
        </dd>
      </dl>
      {job.error && <p className="text-xs text-red-800">Error: {job.error}</p>}
      {job.checks.length > 0 && (
        <ul aria-label={`${LABEL[job.job]} code checks`} className="space-y-0.5 text-sm">
          {job.checks.map((c) => (
            <li key={c.name} className={c.passed ? 'text-green-900' : 'text-red-800'}>
              <span className="sr-only">{c.passed ? 'Passed: ' : 'Failed: '}</span>
              {c.passed ? '✓' : '✗'} {c.name}
              {c.detail ? ` — ${c.detail}` : ''}
            </li>
          ))}
        </ul>
      )}
      {!skipped && (
        <details className="text-sm">
          <summary className="cursor-pointer">Output ({job.job})</summary>
          <pre className="max-h-64 overflow-auto rounded bg-slate-100 p-2 text-xs whitespace-pre-wrap">
            {JSON.stringify(job.output, null, 2)}
          </pre>
        </details>
      )}
      {!skipped && terminal && (
        <div className="space-y-1">
          <button
            type="button"
            className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50"
            aria-expanded={open}
            onClick={() => void toggleMessages()}
          >
            {open ? `Hide messages (${job.job})` : `Show messages (${job.job})`}
          </button>
          {open && loading && (
            <p role="status" className="text-xs text-slate-600">
              Loading messages…
            </p>
          )}
          {open && msgError && (
            <p role="alert" className="text-xs text-red-800">
              {msgError}
            </p>
          )}
          {open && msgs && msgs.length === 0 && (
            <p className="text-xs text-slate-600">No messages recorded.</p>
          )}
          {open &&
            msgs?.map((m, i) => (
              <div key={i} className="text-xs">
                <div className="font-medium">{m.role}</div>
                <pre className="max-h-64 overflow-auto rounded bg-slate-100 p-2 whitespace-pre-wrap">
                  {text(m.content)}
                </pre>
              </div>
            ))}
        </div>
      )}
      {skipped ? (
        <p className="text-xs text-slate-600">Skipped: no feedback for this job.</p>
      ) : (
        <FeedbackControls
          runId={runId}
          job={job.job}
          feedback={job.feedback}
          disabled={!terminal}
          wrongVerdicts={wrongVerdicts}
          onSaved={(fb) => onFeedback(job.job, fb)}
        />
      )}
    </article>
  )
}
