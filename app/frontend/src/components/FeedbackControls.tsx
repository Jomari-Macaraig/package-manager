import { useState } from 'react'
import { ApiError } from '../api/client'
import { putFeedback } from '../api/v2'
import type { Feedback, JobName } from '../api/typesV2'

type Props = {
  runId: string
  job: JobName
  feedback: Feedback | null
  /** feedback is only accepted once the run is terminal */
  disabled?: boolean
  /** check job only: the verdict indices marked wrong in the verdicts list */
  wrongVerdicts?: number[]
  onSaved: (fb: Feedback) => void
}

const MAX_COMMENT = 2000

export function FeedbackControls({ runId, job, feedback, disabled, wrongVerdicts, onSaved }: Props) {
  const [rating, setRating] = useState<'up' | 'down' | null>(feedback?.rating ?? null)
  const [comment, setComment] = useState(feedback?.comment ?? '')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggle = (r: 'up' | 'down') => {
    setRating((cur) => (cur === r ? null : r))
    setSaved(false)
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const fb = await putFeedback(runId, job, {
        rating,
        comment,
        ...(job === 'check' ? { wrong_verdicts: wrongVerdicts ?? [] } : {}),
      })
      onSaved(fb)
      setSaved(true)
    } catch (e) {
      setError(
        e instanceof ApiError ? e.messages.join('; ') : e instanceof Error ? e.message : String(e),
      )
    } finally {
      setSaving(false)
    }
  }

  const btn = (r: 'up' | 'down', word: string, emoji: string) => (
    <button
      type="button"
      aria-pressed={rating === r}
      disabled={disabled || saving}
      className={
        'rounded border px-2 py-0.5 text-sm disabled:opacity-50 ' +
        (rating === r ? 'border-blue-700 bg-blue-100 font-semibold' : 'border-slate-300 hover:bg-slate-50')
      }
      onClick={() => toggle(r)}
    >
      <span aria-hidden="true">{emoji}</span> {word}
    </button>
  )

  return (
    <div className="space-y-1" role="group" aria-label={`Feedback on the ${job} job`}>
      <div className="flex flex-wrap items-center gap-2">
        {btn('up', 'Good', '👍')}
        {btn('down', 'Bad', '👎')}
      </div>
      <label className="block text-xs text-slate-700">
        Comment on the {job} job
        <textarea
          className="mt-0.5 block w-full rounded border border-slate-300 px-2 py-1 text-sm"
          rows={2}
          maxLength={MAX_COMMENT}
          value={comment}
          disabled={disabled || saving}
          onChange={(e) => {
            setComment(e.target.value)
            setSaved(false)
          }}
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded border border-slate-400 px-2 py-0.5 text-sm hover:bg-slate-100 disabled:opacity-50"
          disabled={disabled || saving}
          onClick={() => void save()}
        >
          Save feedback ({job})
        </button>
        {saved && (
          <span role="status" className="text-xs text-green-800">
            Saved
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="text-xs text-red-800">
          {error}
        </p>
      )}
    </div>
  )
}
