import { useCallback, useEffect, useRef, useState } from 'react'
import { cancelRun, getRun, isTerminal, subscribe } from '../api/client'
import type { Run, RunEvent, RunStatus } from '../api/types'

export type BaseEvent = { id: number; type: string }
export type BaseRun = {
  run_id: string
  status: RunStatus
  events: BaseEvent[]
}

/** The three calls a flow needs; v1's are the default, v2 passes its own. */
export type RunApi<R extends BaseRun, E extends BaseEvent> = {
  getRun: (id: string) => Promise<R>
  subscribe: (
    runId: string,
    onEvent: (e: E) => void,
    onClosed: (run: R | null) => void,
    fromId?: number,
  ) => () => void
  cancelRun: (id: string) => Promise<void>
}

/**
 * v1's calls as lazy wrappers over the imports (not captured references), so a
 * module-level `vi.mock('./api/client')` still applies.
 */
export const v1Api: RunApi<Run, RunEvent> = {
  getRun: (id) => getRun(id),
  subscribe: (...a) => subscribe(...a),
  cancelRun: (id) => cancelRun(id),
}

export type RunFlow<R extends BaseRun = Run, E extends BaseEvent = RunEvent> = {
  /** the run being watched or last finished (seeded from getRun) */
  run: R | null
  events: E[]
  running: boolean
  cancelling: boolean
  error: string | null
  /** attach per the SSE rule: getRun, seed log, then subscribe from lastId + 1 */
  attach: (runId: string) => Promise<void>
  cancel: () => Promise<void>
}

export function useRunFlow<R extends BaseRun = Run, E extends BaseEvent = RunEvent>(
  onFinished: () => void,
  api: RunApi<R, E> = v1Api as unknown as RunApi<R, E>,
): RunFlow<R, E> {
  const [run, setRun] = useState<R | null>(null)
  const [events, setEvents] = useState<E[]>([])
  const [running, setRunning] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const unsub = useRef<(() => void) | null>(null)
  const token = useRef(0)
  const finishedCb = useRef(onFinished)
  finishedCb.current = onFinished
  const apiRef = useRef(api)
  apiRef.current = api

  const stop = useCallback(() => {
    unsub.current?.()
    unsub.current = null
  }, [])

  useEffect(() => stop, [stop])

  const attach = useCallback(
    async (runId: string) => {
      stop()
      const mine = ++token.current
      setError(null)
      setCancelling(false)
      let initial: R
      try {
        initial = await apiRef.current.getRun(runId)
      } catch (e) {
        if (mine !== token.current) return
        setError(e instanceof Error ? e.message : 'Could not load the run')
        setRunning(false)
        return
      }
      if (mine !== token.current) return
      setRun(initial)
      setEvents(initial.events as E[])
      if (isTerminal(initial.status)) {
        setRunning(false)
        return
      }
      setRunning(true)
      const lastId = initial.events.length ? initial.events[initial.events.length - 1].id : -1
      unsub.current = apiRef.current.subscribe(
        runId,
        (ev) => {
          if (mine !== token.current) return
          setEvents((cur) => (cur.some((c) => c.id === ev.id) ? cur : [...cur, ev]))
        },
        (final) => {
          if (mine !== token.current) return
          unsub.current = null
          setRunning(false)
          setCancelling(false)
          if (final) {
            setRun(final)
            setEvents(final.events as E[])
          } else {
            setError('Lost connection to the server; reload to see the final state')
          }
          finishedCb.current()
        },
        lastId + 1,
      )
    },
    [stop],
  )

  const cancel = useCallback(async () => {
    if (!run) return
    setCancelling(true)
    try {
      await apiRef.current.cancelRun(run.run_id)
    } catch (e) {
      setCancelling(false)
      setError(e instanceof Error ? e.message : 'Cancel failed')
    }
  }, [run])

  return { run, events, running, cancelling, error, attach, cancel }
}
