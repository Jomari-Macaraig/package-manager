import { useCallback, useEffect, useRef, useState } from 'react'
import { cancelRun, getRun, isTerminal, subscribe } from '../api/client'
import type { Run, RunEvent } from '../api/types'

export type RunFlow = {
  /** the run being watched or last finished (seeded from getRun) */
  run: Run | null
  events: RunEvent[]
  running: boolean
  cancelling: boolean
  error: string | null
  /** attach per the SSE rule: getRun, seed log, then subscribe from lastId + 1 */
  attach: (runId: string) => Promise<void>
  cancel: () => Promise<void>
}

export function useRunFlow(onFinished: () => void): RunFlow {
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [running, setRunning] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const unsub = useRef<(() => void) | null>(null)
  const token = useRef(0)
  const finishedCb = useRef(onFinished)
  finishedCb.current = onFinished

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
      let initial: Run
      try {
        initial = await getRun(runId)
      } catch (e) {
        if (mine !== token.current) return
        setError(e instanceof Error ? e.message : 'Could not load the run')
        setRunning(false)
        return
      }
      if (mine !== token.current) return
      setRun(initial)
      setEvents(initial.events)
      if (isTerminal(initial.status)) {
        setRunning(false)
        return
      }
      setRunning(true)
      const lastId = initial.events.length ? initial.events[initial.events.length - 1].id : -1
      unsub.current = subscribe(
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
            setEvents(final.events)
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
      await cancelRun(run.run_id)
    } catch (e) {
      setCancelling(false)
      setError(e instanceof Error ? e.message : 'Cancel failed')
    }
  }, [run])

  return { run, events, running, cancelling, error, attach, cancel }
}
