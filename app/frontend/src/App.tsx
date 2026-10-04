import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
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
import type { Config, Sample } from './api/types'
import type { ConfigV2, JobChoices, RunSummaryV2, RunV2, SampleV2 } from './api/typesV2'
import {
  createRunV2,
  getActiveRunV2,
  getConfigV2,
  getRunV2,
  getSamplesV2,
  listRunsV2,
  v2Api,
} from './api/v2'
import { BoxesTable } from './components/BoxesTable'
import { EvaluationPage } from './components/EvaluationPage'
import { HistoryList } from './components/HistoryList'
import { InstructionsBox } from './components/InstructionsBox'
import { ItemsTable } from './components/ItemsTable'
import { JobSettings } from './components/JobSettings'
import { ModelSettings } from './components/ModelSettings'
import { RunControls } from './components/RunControls'
import { RunView } from './components/RunView'
import { RunViewV2 } from './components/RunViewV2'
import { SamplePicker } from './components/SamplePicker'
import { useRunFlow } from './hooks/useRunFlow'
import {
  boxRowsFromInput,
  defaultJobChoices,
  emptyBoxRow,
  emptyItemRow,
  itemRowsFromInput,
  toRequest,
  toRequestV2,
  validateEditor,
  type BoxRow,
  type ItemRow,
  type ModelChoice,
} from './lib/editor'
import { formatEventV2 } from './lib/events'
import type { Run } from './api/types'

const msg = (e: unknown) =>
  e instanceof ApiError ? e.messages.join('; ') : e instanceof Error ? e.message : String(e)

type Pipeline = 'v1' | 'v2'
type Tab = 'playground' | 'evaluation'

const PIPELINE_KEY = 'packing-playground.pipeline'
// v2 is the default; a saved "v1" choice is kept
const readPipeline = (): Pipeline => {
  try {
    return localStorage.getItem(PIPELINE_KEY) === 'v1' ? 'v1' : 'v2'
  } catch {
    return 'v2'
  }
}
const savePipeline = (p: Pipeline) => {
  try {
    localStorage.setItem(PIPELINE_KEY, p)
  } catch {
    /* storage unavailable: the choice just isn't remembered */
  }
}

/** A history list with its own loading and error state. */
function useHistory<T>(list: () => Promise<T[]>) {
  const [runs, setRuns] = useState<T[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setRuns(await list())
      setError(null)
    } catch (e) {
      setError(msg(e))
    } finally {
      setLoading(false)
    }
  }, [list])
  return { runs, loading, error, setError, refresh }
}

const v2Meta = (r: RunSummaryV2) => (
  <div className="text-xs">
    {r.status} · {r.total_cost == null ? 'cost —' : `$${r.total_cost.toFixed(4)}`} ·{' '}
    {r.source ? `chosen: ${r.source}` : 'no plan chosen'}
    {r.reason ? ` · ${r.reason}` : ''}
    {r.over_budget ? ' · over budget' : ''}
    {r.fake ? ' · fake' : ''}
  </div>
)

export default function App() {
  const [pipeline, setPipeline] = useState<Pipeline>(readPipeline)
  const [tab, setTab] = useState<Tab>('playground')

  const [config, setConfig] = useState<Config | null>(null)
  const [samples, setSamples] = useState<Sample[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  // v2 config and samples load the first time v2 is selected
  const [configV2, setConfigV2] = useState<ConfigV2 | null>(null)
  const [samplesV2, setSamplesV2] = useState<SampleV2[]>([])
  const [loadErrorV2, setLoadErrorV2] = useState<string | null>(null)
  const v2Started = useRef(false)

  const [items, setItems] = useState<ItemRow[]>(() => [emptyItemRow()])
  const [boxes, setBoxes] = useState<BoxRow[]>(() => [emptyBoxRow()])
  const [instructions, setInstructions] = useState('')
  const [models, setModels] = useState<ModelChoice | null>(null)
  const [jobs, setJobs] = useState<JobChoices | null>(null)
  const dirty = useRef(false)

  const historyV1 = useHistory(listRuns)
  const historyV2 = useHistory(listRunsV2)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [pastRun, setPastRun] = useState<Run | null>(null)
  const [pastRunV2, setPastRunV2] = useState<RunV2 | null>(null)

  const [packErrors, setPackErrors] = useState<string[]>([])
  const [conflict, setConflict] = useState<{ kind: Pipeline | null } | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const refreshV1 = historyV1.refresh
  const refreshV2 = historyV2.refresh
  const flow = useRunFlow(() => void refreshV1())
  const flowV2 = useRunFlow<RunV2, RunV2['events'][number]>(() => void refreshV2(), v2Api)
  const { attach } = flow
  const attachV2 = flowV2.attach
  const running = flow.running || flowV2.running

  /** Switch the pipeline. A past run belongs to one pipeline, so it is cleared. */
  const choosePipeline = useCallback((p: Pipeline, persist = true) => {
    setPipeline(p)
    if (persist) savePipeline(p)
    setPastRun(null)
    setPastRunV2(null)
  }, [])

  const loadSample = useCallback((s: { items: Sample['items']; boxes: Sample['boxes']; instructions: string }) => {
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
        start: 'improve',
      })
      if (!dirty.current && s.length) loadSample(s[0])
    } catch (e) {
      setLoadError(msg(e))
    }
  }, [loadSample])

  const loadBootstrapV2 = useCallback(async () => {
    setLoadErrorV2(null)
    try {
      const [c, s] = await Promise.all([getConfigV2(), getSamplesV2()])
      setConfigV2(c)
      setSamplesV2(s)
      setJobs((cur) => cur ?? defaultJobChoices(c))
    } catch (e) {
      setLoadErrorV2(msg(e))
    }
  }, [])

  useEffect(() => {
    void loadBootstrap()
    void refreshV1()
    // The silent v2 check: failures are swallowed, no alert and no shared error
    // state (an unreachable server is covered by the bootstrap error banner).
    const checkV2 = () =>
      getActiveRunV2().then(
        (a) => {
          if (a.run_id) {
            choosePipeline('v2', false)
            void attachV2(a.run_id)
          }
        },
        () => {},
      )
    const checkV1 = getActiveRun().then(
      (a) => {
        if (a.run_id) {
          void attach(a.run_id)
          return true
        }
        return false
      },
      () => false,
    )
    if (pipeline === 'v2') void checkV2()
    else
      void checkV1.then((found) => {
        if (!found) void checkV2()
      })
    // run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (pipeline !== 'v2' || v2Started.current) return
    v2Started.current = true
    void loadBootstrapV2()
    void refreshV2()
  }, [pipeline, loadBootstrapV2, refreshV2])

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

  const ready = pipeline === 'v1' ? !!models : !!jobs
  const canPack = !!config && ready && validation.valid && !running && !submitting

  const pack = async () => {
    if (!config || !canPack) return
    setPackErrors([])
    setConflict(null)
    setSubmitting(true)
    try {
      if (pipeline === 'v1' && models) {
        const { run_id } = await createRun(toRequest(items, boxes, instructions, models))
        setPastRun(null)
        await attach(run_id)
      } else if (pipeline === 'v2' && jobs) {
        const { run_id } = await createRunV2(toRequestV2(items, boxes, instructions, jobs))
        setPastRunV2(null)
        await attachV2(run_id)
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409)
        setConflict({ kind: e.kind === 'v1' || e.kind === 'v2' ? e.kind : null })
      else setPackErrors(e instanceof ApiError ? e.messages : [msg(e)])
    } finally {
      setSubmitting(false)
    }
  }

  const showActive = async () => {
    const target = conflict?.kind ?? pipeline
    try {
      const a = target === 'v1' ? await getActiveRun() : await getActiveRunV2()
      if (a.run_id) {
        setConflict(null)
        if (target !== pipeline) choosePipeline(target)
        else {
          setPastRun(null)
          setPastRunV2(null)
        }
        setTab('playground')
        await (target === 'v1' ? attach(a.run_id) : attachV2(a.run_id))
      } else {
        setConflict(null)
        setPackErrors(['The other run has already finished. Try Pack again.'])
      }
    } catch (e) {
      setPackErrors([msg(e)])
    }
  }

  const openPast = async (id: string) => {
    const history = pipeline === 'v1' ? historyV1 : historyV2
    try {
      if (pipeline === 'v1') {
        const r = await getRun(id)
        if (isTerminal(r.status)) {
          setPastRun(r)
        } else {
          setPastRun(null)
          await attach(id)
        }
      } else {
        const r = await getRunV2(id)
        if (isTerminal(r.status)) {
          setPastRunV2(r)
        } else {
          setPastRunV2(null)
          await attachV2(id)
        }
      }
      setHistoryOpen(false)
    } catch (e) {
      history.setError(msg(e))
    }
  }

  const shown = pastRun ?? flow.run
  const shownV2 = pastRunV2 ?? flowV2.run
  const activeShown = pipeline === 'v1' ? shown : shownV2
  const activePast = pipeline === 'v1' ? pastRun : pastRunV2
  const closePast = () => (pipeline === 'v1' ? setPastRun(null) : setPastRunV2(null))
  const flowError = pipeline === 'v1' ? flow.error : flowV2.error

  const onTabKey = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    const next: Tab = tab === 'playground' ? 'evaluation' : 'playground'
    setTab(next)
    document.getElementById(`tab-${next}`)?.focus()
  }

  const tabButton = (t: Tab, label: string) => (
    <button
      type="button"
      role="tab"
      id={`tab-${t}`}
      aria-selected={tab === t}
      aria-controls={`panel-${t}`}
      tabIndex={tab === t ? 0 : -1}
      className={
        'border-b-2 px-3 py-1 text-sm ' +
        (tab === t ? 'border-blue-700 font-semibold' : 'border-transparent text-slate-600 hover:bg-slate-50')
      }
      onClick={() => setTab(t)}
      onKeyDown={onTabKey}
    >
      {label}
    </button>
  )

  const pipelineButton = (p: Pipeline) => (
    <button
      type="button"
      aria-pressed={pipeline === p}
      className={
        'px-2.5 py-0.5 text-sm ' +
        (pipeline === p ? 'bg-slate-800 font-semibold text-white' : 'hover:bg-slate-100')
      }
      onClick={() => {
        if (p === pipeline) return
        choosePipeline(p)
        setConflict(null)
        setPackErrors([])
      }}
    >
      {p}
    </button>
  )

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white px-4 py-2">
        <h1 className="text-lg font-semibold">Packing Playground</h1>
        <div className="flex flex-wrap items-center gap-4">
          <div role="group" aria-label="Pipeline" className="flex items-center gap-2 text-sm">
            <span aria-hidden="true">Pipeline:</span>
            <div className="flex overflow-hidden rounded border border-slate-300">
              {pipelineButton('v1')}
              {pipelineButton('v2')}
            </div>
          </div>
          <div role="tablist" aria-label="Page" className="flex">
            {tabButton('playground', 'Playground')}
            {tabButton('evaluation', 'Evaluation')}
          </div>
          <button
            type="button"
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
            aria-expanded={historyOpen}
            aria-controls="history-drawer"
            onClick={() => setHistoryOpen((o) => !o)}
          >
            History
          </button>
        </div>
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

      <div
        role="tabpanel"
        id="panel-playground"
        aria-labelledby="tab-playground"
        hidden={tab !== 'playground'}
        className="grid gap-6 p-4 lg:grid-cols-2"
      >
        <main aria-label="Editor" className="min-w-0 space-y-5">
          {config && ready ? (
            <>
              {pipeline === 'v1' ? (
                <SamplePicker
                  key="v1"
                  samples={samples}
                  edited={dirty.current}
                  onLoad={loadSample}
                  disabled={running}
                />
              ) : (
                <SamplePicker
                  key="v2"
                  samples={samplesV2}
                  edited={dirty.current}
                  onLoad={loadSample}
                  disabled={running}
                />
              )}
              <ItemsTable
                rows={items}
                onChange={touch(setItems)}
                caps={config.caps}
                validation={validation}
                disabled={running}
              />
              <BoxesTable
                rows={boxes}
                onChange={touch(setBoxes)}
                caps={config.caps}
                validation={validation}
                disabled={running}
              />
              <InstructionsBox
                value={instructions}
                onChange={touch(setInstructions)}
                max={config.caps.max_instruction_chars}
                disabled={running}
              />
              {pipeline === 'v1' && models && (
                <ModelSettings
                  config={config}
                  value={models}
                  onChange={setModels}
                  disabled={running}
                />
              )}
              {pipeline === 'v2' && configV2 && jobs && (
                <JobSettings config={configV2} value={jobs} onChange={setJobs} disabled={running} />
              )}
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
          ) : pipeline === 'v2' && loadErrorV2 ? (
            <div role="alert" className="flex items-center gap-3 text-sm text-red-900">
              <span>Could not load the v2 configuration: {loadErrorV2}</span>
              <button
                type="button"
                className="rounded border border-red-700 px-2 py-0.5"
                onClick={() => void loadBootstrapV2()}
              >
                Retry v2 configuration
              </button>
            </div>
          ) : (
            !loadError && <p className="text-sm text-slate-600">Loading…</p>
          )}

          {pipeline === 'v1' ? (
            <RunControls
              canPack={canPack}
              running={flow.running}
              cancelling={flow.cancelling}
              onPack={() => void pack()}
              onCancel={() => void flow.cancel()}
              events={flow.events}
            />
          ) : (
            <RunControls
              canPack={canPack}
              running={flowV2.running}
              cancelling={flowV2.cancelling}
              onPack={() => void pack()}
              onCancel={() => void flowV2.cancel()}
              events={flowV2.events}
              format={formatEventV2}
              packerHint={false}
            />
          )}

          {conflict && (
            <div role="alert" className="flex items-center gap-3 text-sm text-amber-900">
              <span>A run is already in progress</span>
              {conflict.kind && <span>(a {conflict.kind} run)</span>}
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
          {flowError && (
            <p role="alert" className="text-sm text-red-800">
              {flowError}
            </p>
          )}
        </main>

        <section aria-label="Result" className="min-w-0 space-y-3">
          {activePast && (
            <div className="flex items-center justify-between rounded bg-slate-200 px-3 py-1 text-sm">
              <span>Viewing a past run (read-only)</span>
              <button
                type="button"
                className="rounded border border-slate-500 px-2 py-0.5"
                onClick={closePast}
              >
                Close
              </button>
            </div>
          )}
          {activeShown ? (
            pipeline === 'v1' ? (
              <RunView run={shown as Run} />
            ) : (
              <RunViewV2
                key={shownV2!.run_id}
                run={shownV2!}
                events={pastRunV2 ? undefined : flowV2.events}
              />
            )
          ) : (
            <p className="text-sm text-slate-600">
              Results appear here after you pack an order.
            </p>
          )}
        </section>
      </div>

      <div
        role="tabpanel"
        id="panel-evaluation"
        aria-labelledby="tab-evaluation"
        hidden={tab !== 'evaluation'}
      >
        {tab === 'evaluation' && <EvaluationPage />}
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
        {pipeline === 'v1' ? (
          <HistoryList
            runs={historyV1.runs}
            loading={historyV1.loading}
            error={historyV1.error}
            selectedId={shown?.run_id ?? null}
            onOpen={(id) => void openPast(id)}
            onRefresh={() => void refreshV1()}
          />
        ) : (
          <HistoryList
            runs={historyV2.runs}
            loading={historyV2.loading}
            error={historyV2.error}
            selectedId={shownV2?.run_id ?? null}
            onOpen={(id) => void openPast(id)}
            onRefresh={() => void refreshV2()}
            renderMeta={v2Meta}
          />
        )}
      </aside>
    </div>
  )
}
