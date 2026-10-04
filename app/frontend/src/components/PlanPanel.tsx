import { useState } from 'react'
import { renderUrl, svgDownloadUrl } from '../api/client'
import type { ModelSide, PlanSide } from '../api/types'

type Props = {
  title: string
  planKey: 'model' | 'packer'
  side: PlanSide | ModelSide | null
  runId: string
  /** images and downloads are only offered once the run is terminal */
  terminal: boolean
  chosen: boolean
}

const planLabel = (k: 'model' | 'packer') => (k === 'model' ? 'model plan' : 'code packer plan')

export function PlanPanel({ title, planKey, side, runId, terminal, chosen }: Props) {
  const [boxSel, setBoxSel] = useState(1)
  const [stepSel, setStepSel] = useState<number | null>(null) // null = follow max
  const [failedSrc, setFailedSrc] = useState<string | null>(null)

  const heading = (
    <div className="flex items-center justify-between gap-2">
      <h3 className="font-semibold">{title}</h3>
      {chosen && (
        <span className="rounded bg-green-100 px-2 py-0.5 text-xs font-semibold text-green-900">
          Chosen
        </span>
      )}
    </div>
  )
  const frame =
    'space-y-2 rounded border p-3 ' + (chosen ? 'border-2 border-green-600' : 'border-slate-300')

  if (!side) {
    return (
      <section className={frame} aria-label={title}>
        {heading}
        <p className="text-sm text-slate-600">No result for this side.</p>
      </section>
    )
  }

  if (!side.ok || !side.plan) {
    return (
      <section className={frame} aria-label={title}>
        {heading}
        <p className="text-sm font-medium text-red-800">Invalid plan</p>
        {side.errors.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-5 text-sm" aria-label={`${title} errors`}>
            {side.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        )}
      </section>
    )
  }

  const plan = side.plan
  const rank = side.rank
  const boxCount = plan.boxes.length
  const box = Math.min(Math.max(boxSel, 1), Math.max(boxCount, 1))
  const maxStep = boxCount ? plan.boxes[box - 1].items.length : 0
  const step = stepSel == null ? maxStep : Math.min(Math.max(stepSel, 1), maxStep)
  const src = boxCount && maxStep ? renderUrl(runId, planKey, box, step) : null

  return (
    <section className={frame} aria-label={title}>
      {heading}
      {rank && (
        <dl className="grid grid-cols-2 gap-x-3 text-xs text-slate-700">
          <dt>Leftover units</dt>
          <dd>{rank[0]}</dd>
          <dt>Boxes</dt>
          <dd>{rank[1]}</dd>
          <dt>Shipped volume (in³)</dt>
          <dd>{rank[2]}</dd>
          <dt>Fill height (in)</dt>
          <dd>{rank[3]}</dd>
        </dl>
      )}
      {'rounds' in side && (
        <p className="text-xs text-slate-600">
          Rounds: {side.rounds} · Improved: {side.improved ? 'yes' : 'no'}
        </p>
      )}
      {boxCount === 0 ? (
        <p className="text-sm">No boxes used</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label>
              Box{' '}
              <select
                className="rounded border border-slate-300 px-1 py-0.5"
                aria-label={`Box (${planLabel(planKey)})`}
                value={box}
                onChange={(e) => setBoxSel(Number(e.target.value))}
              >
                {plan.boxes.map((b, i) => (
                  <option key={i} value={i + 1}>
                    Box {i + 1} — {b.name}
                  </option>
                ))}
              </select>
            </label>
            {maxStep > 0 && (
              <label className="flex items-center gap-2">
                Loading step
                <input
                  type="range"
                  min={1}
                  max={maxStep}
                  step={1}
                  value={step}
                  aria-label={`Loading step (${planLabel(planKey)})`}
                  aria-valuetext={`Step ${step} of ${maxStep}`}
                  onChange={(e) => setStepSel(Number(e.target.value))}
                />
                <span className="text-xs text-slate-600">
                  {step} / {maxStep}
                </span>
              </label>
            )}
          </div>
          {terminal && src ? (
            failedSrc === src ? (
              <p className="text-sm text-red-800">Image could not be loaded.</p>
            ) : (
              <img
                src={src}
                alt={`Box ${box} of the ${planLabel(planKey)}, steps 1–${step}`}
                className="w-full rounded border border-slate-200 bg-white"
                onError={() => setFailedSrc(src)}
              />
            )
          ) : (
            !terminal && <p className="text-sm text-slate-600">Images appear when the run finishes.</p>
          )}
          {terminal && (
            <a
              className="inline-block text-sm text-blue-800 underline"
              href={svgDownloadUrl(runId, planKey)}
              download
            >
              Download SVG
            </a>
          )}
        </>
      )}
    </section>
  )
}
