import type { Feedback, JobName, JobView, RunEventV2 } from '../api/typesV2'
import { formatEventV2 } from '../lib/events'
import { JobCard } from './JobCard'

type Props = {
  runId: string
  jobs: JobView[]
  terminal: boolean
  /** the run's events; while it is going, the job events are the progress */
  events: RunEventV2[]
  wrongVerdicts: number[]
  onFeedback: (job: JobName, fb: Feedback) => void
}

export function JobsPanel({ runId, jobs, terminal, events, wrongVerdicts, onFeedback }: Props) {
  const jobEvents = events.filter((e) => e.type === 'job')
  return (
    <section aria-labelledby="jobs-heading" className="space-y-2">
      <h2 id="jobs-heading" className="text-base font-semibold">
        Jobs
      </h2>
      {jobs.length === 0 ? (
        terminal ? (
          <p className="text-sm text-slate-600">No job data</p>
        ) : jobEvents.length === 0 ? (
          <p className="text-sm text-slate-600">Jobs appear as they finish.</p>
        ) : (
          <ul className="list-disc pl-5 text-sm" aria-label="Job progress">
            {jobEvents.map((e) => (
              <li key={e.id}>{formatEventV2(e)}</li>
            ))}
          </ul>
        )
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {jobs.map((j) => (
            <JobCard
              key={j.job}
              runId={runId}
              job={j}
              terminal={terminal}
              wrongVerdicts={j.job === 'check' ? wrongVerdicts : undefined}
              onFeedback={onFeedback}
            />
          ))}
        </div>
      )}
    </section>
  )
}
