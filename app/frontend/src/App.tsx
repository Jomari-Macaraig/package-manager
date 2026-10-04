import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  createRun,
  getActiveRun,
  getConfig,
  getRun,
  getSamples,
  isTerminal,
  listRuns,
} from './api/client'
import type { Config, Run, RunSummary, Sample } from './api/types'
import { BoxesTable } from './components/BoxesTable'
import { HistoryList } from './components/HistoryList'
import { InstructionsBox } from './components/InstructionsBox'
import { ItemsTable } from './components/ItemsTable'
import { ModelSettings } from './components/ModelSettings'
import { RunControls } from './components/RunControls'
import { RunView } from './components/RunView'
import { SamplePicker } from './components/SamplePicker'
import { useRunFlow } from './hooks/useRunFlow'
import {
  boxRowsFromInput,
  emptyBoxRow,
  emptyItemRow,
  itemRowsFromInput,
  toRequest,
  validateEditor,
  type BoxRow,
  type ItemRow,
  type ModelChoice,
} from './lib/editor'

const msg = (e: unknown) =>
  e instanceof ApiError ? e.messages.join('; ') : e instanceof Error ? e.message : String(e)

export default function App() {
  const [config, setConfig] = useState<Config | null>(null)
  const [samples, setSamples] = useState<Sample[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  const [items, setItems] = useState<ItemRow[]>(() => [emptyItemRow()])
  const [boxes, setBoxes] = useState<BoxRow[]>(() => [emptyBoxRow()])
  const [instructions, setInstructions] = useState('')
  const [models, setModels] = useState<ModelChoice | null>(null)
  const dirty = useRef(false)

  const [history, setHistory] = useState<RunSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [pastRun, setPastRun] = useState<Run | null>(null)

  const [packErrors, setPackErrors] = useState<string[]>([])
  const [conflict, setConflict] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const refreshHistory = useCallback(async () => {
    setHistoryLoading(true)
    try {
      setHistory(await listRuns())
      setHistoryError(null)
    } catch (e) {
      setHistoryError(msg(e))
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  const flow = useRunFlow(() => void refreshHistory())
  const { attach } = flow

  const loadSample = useCallback((s: Sample) => {
    setItems(itemRowsFromInput(s.items))
    setBoxes(boxRowsFromInput(s.boxes))
    setInstructions(s.instructions)
    dirty.current = false
  }, [])

  const loadBootstrap = useCallback(async () => {
    setLoadError(null)
    try {
      const [c, s] = await Promise.all([getConfig(), getSamples()])
      setConfig(c)
      setSamples(s)
      setModels({
        extractionModel: c.defaults.extraction_model,
        planningModel: c.defaults.planning_model,
        effort: c.defaults.reasoning_effort,
      })
      if (!dirty.current && s.length) loadSample(s[0])
    } catch (e) {
      setLoadError(msg(e))
    }
  }, [loadSample])

  useEffect(() => {
    void loadBootstrap()
    void refreshHistory()
    getActiveRun().then(
      (a) => {
        if (a.run_id) void attach(a.run_id)
      },
      () => {
        /* the bootstrap error banner covers an unreachable server */
      },
    )
    // run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const validation = useMemo(
    () =>
      config
        ? validateEditor(items, boxes, instructions, config.caps)
        : { cells: {}, messages: [], units: 0, valid: false },
    [config, items, boxes, instructions],
  )

  const touch = <T,>(set: (v: T) => void) => (v: T) => {
    dirty.current = true
    set(v)
  }

  const canPack = !!config && !!models && validation.valid && !flow.running && !submitting

  const pack = async () => {
    if (!config || !models || !canPack) return
    setPackErrors([])
    setConflict(false)
    setSubmitting(true)
    try {
      const { run_id } = await createRun(toRequest(items, boxes, instructions, models))
      setPastRun(null)
      await attach(run_id)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict(true)
      else setPackErrors(e instanceof ApiError ? e.messages : [msg(e)])
    } finally {
      setSubmitting(false)
    }
  }

  const showActive = async () => {
    try {
      const a = await getActiveRun()
      if (a.run_id) {
        setPastRun(null)
        setConflict(false)
        await attach(a.run_id)
      } else {
        setConflict(false)
        setPackErrors(['The other run has already finished. Try Pack again.'])
      }
    } catch (e) {
      setPackErrors([msg(e)])
    }
  }

  const openPast = async (id: string) => {
    try {
      const r = await getRun(id)
      if (isTerminal(r.status)) {
        setPastRun(r)
      } else {
        setPastRun(null)
        await attach(id)
      }
      setHistoryOpen(false)
    } catch (e) {
      setHistoryError(msg(e))
    }
  }

  const shown = pastRun ?? flow.run

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2">
        <h1 className="text-lg font-semibold">Packing Playground</h1>
        <button
          type="button"
          className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
          aria-expanded={historyOpen}
          aria-controls="history-drawer"
          onClick={() => setHistoryOpen((o) => !o)}
        >
          History
        </button>
      </header>

      {loadError && (
        <div role="alert" className="flex items-center gap-3 bg-red-100 px-4 py-2 text-sm text-red-900">
          <span>Could not load configuration: {loadError}</span>
          <button
            type="button"
            className="rounded border border-red-700 px-2 py-0.5"
            onClick={() => void loadBootstrap()}
          >
            Retry
          </button>
        </div>
      )}

      <div className="grid gap-6 p-4 lg:grid-cols-2">
        <main aria-label="Editor" className="min-w-0 space-y-5">
          {config && models ? (
            <>
              <SamplePicker
                samples={samples}
                edited={dirty.current}
                onLoad={loadSample}
                disabled={flow.running}
              />
              <ItemsTable
                rows={items}
                onChange={touch(setItems)}
                caps={config.caps}
                validation={validation}
                disabled={flow.running}
              />
              <BoxesTable
                rows={boxes}
                onChange={touch(setBoxes)}
                caps={config.caps}
                validation={validation}
                disabled={flow.running}
              />
              <InstructionsBox
                value={instructions}
                onChange={touch(setInstructions)}
                max={config.caps.max_instruction_chars}
                disabled={flow.running}
              />
              <ModelSettings
                config={config}
                value={models}
                onChange={setModels}
                disabled={flow.running}
              />
              {!validation.valid && (
                <div className="text-sm text-red-800">
                  <p className="font-medium">Fix these before packing:</p>
                  <ul className="list-disc pl-5">
                    {validation.messages.map((m, i) => (
                      <li key={i}>{m}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          ) : (
            !loadError && <p className="text-sm text-slate-600">Loading…</p>
          )}

          <RunControls
            canPack={canPack}
            running={flow.running}
            cancelling={flow.cancelling}
            onPack={() => void pack()}
            onCancel={() => void flow.cancel()}
            events={flow.events}
          />

          {conflict && (
            <div role="alert" className="flex items-center gap-3 text-sm text-amber-900">
              <span>A run is already in progress</span>
              <button
                type="button"
                className="rounded border border-amber-700 px-2 py-0.5"
                onClick={() => void showActive()}
              >
                Show it
              </button>
            </div>
          )}
          {packErrors.length > 0 && (
            <ul role="alert" className="list-disc pl-5 text-sm text-red-800">
              {packErrors.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          )}
          {flow.error && (
            <p role="alert" className="text-sm text-red-800">
              {flow.error}
            </p>
          )}
        </main>

        <section aria-label="Result" className="min-w-0 space-y-3">
          {pastRun && (
            <div className="flex items-center justify-between rounded bg-slate-200 px-3 py-1 text-sm">
              <span>Viewing a past run (read-only)</span>
              <button
                type="button"
                className="rounded border border-slate-500 px-2 py-0.5"
                onClick={() => setPastRun(null)}
              >
                Close
              </button>
            </div>
          )}
          {shown ? (
            <RunView run={shown} />
          ) : (
            <p className="text-sm text-slate-600">
              Results appear here after you pack an order.
            </p>
          )}
        </section>
      </div>

      <aside
        id="history-drawer"
        aria-label="Run history"
        hidden={!historyOpen}
        className="fixed top-0 right-0 z-10 h-full w-96 max-w-full overflow-y-auto border-l border-slate-300 bg-white p-4 shadow-lg"
      >
        <button
          type="button"
          className="mb-2 rounded border border-slate-300 px-2 py-0.5 text-sm"
          onClick={() => setHistoryOpen(false)}
        >
          Close history
        </button>
        <HistoryList
          runs={history}
          loading={historyLoading}
          error={historyError}
          selectedId={shown?.run_id ?? null}
          onOpen={(id) => void openPast(id)}
          onRefresh={() => void refreshHistory()}
        />
      </aside>
    </div>
  )
}
