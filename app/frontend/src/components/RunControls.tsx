import type { RunEvent } from '../api/types'
import { formatEvent } from '../lib/events'

type ControlEvent = { id: number; t: number; type: string }

type Props<E extends ControlEvent> = {
  canPack: boolean
  running: boolean
  cancelling: boolean
  onPack: () => void
  onCancel: () => void
  events: E[]
  /** one line per event; default: v1's */
  format?: (e: E) => string
  /** v1 only: the "code packer running" wait hint */
  packerHint?: boolean
}

export function RunControls<E extends ControlEvent = RunEvent>({
  canPack,
  running,
  cancelling,
  onPack,
  onCancel,
  events,
  format = formatEvent as unknown as (e: E) => string,
  packerHint = true,
}: Props<E>) {
  const last = events[events.length - 1]
  const waitingOnPacker = packerHint && running && last?.type === 'prepared'

  return (
    <section aria-labelledby="run-heading" className="space-y-2">
      <h2 id="run-heading" className="sr-only">
        Run
      </h2>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded bg-blue-700 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-400"
          disabled={!canPack}
          onClick={onPack}
        >
          Pack
        </button>
        {running && (
          <button
            type="button"
            className="rounded border border-slate-400 px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-60"
            disabled={cancelling}
            onClick={onCancel}
          >
            Cancel
          </button>
        )}
        {cancelling && (
          <span className="text-sm text-slate-700">
            Cancelling… — takes effect before the next model call
          </span>
        )}
      </div>
      <div aria-live="polite" aria-label="Progress" role="log" className="text-sm">
        {events.length > 0 && (
          <ol className="space-y-0.5 rounded border border-slate-200 bg-slate-50 p-2">
            {events.map((e) => (
              <li key={e.id}>
                <span className="mr-2 text-xs text-slate-500">{e.t.toFixed(1)}s</span>
                {format(e)}
              </li>
            ))}
            {waitingOnPacker && (
              <li className="text-slate-600">
                Code packer running — may take up to ~45 s on large orders
              </li>
            )}
          </ol>
        )}
      </div>
    </section>
  )
}
