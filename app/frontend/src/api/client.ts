import type {
  Config,
  Run,
  RunEvent,
  RunHistory,
  RunRequest,
  RunStatus,
  RunSummary,
  Sample,
} from './types'

const BASE = '/api'

/** Error from a non-2xx response. `messages` is the normalized `detail`. */
export class ApiError extends Error {
  status: number
  messages: string[]
  constructor(status: number, messages: string[]) {
    super(messages.join('; '))
    this.name = 'ApiError'
    this.status = status
    this.messages = messages
  }
}

type DetailEntry = { loc?: unknown[]; msg?: string }

export function normalizeDetail(detail: unknown): string[] {
  if (typeof detail === 'string') return [detail]
  if (Array.isArray(detail)) {
    return detail.map((d: DetailEntry | string) => {
      if (typeof d === 'string') return d
      const msg = String(d?.msg ?? '').replace(/^Value error, /, '')
      const loc = Array.isArray(d?.loc) ? d.loc.slice(1).join('.') : ''
      return loc ? `${loc}: ${msg}` : msg
    })
  }
  return []
}

async function toApiError(res: Response): Promise<ApiError> {
  const fallback = `${res.status} ${res.statusText}`.trim()
  let messages: string[] = []
  try {
    const body = await res.json()
    messages = normalizeDetail(body?.detail)
  } catch {
    messages = []
  }
  return new ApiError(res.status, messages.length ? messages : [fallback])
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  let res: Response
  try {
    res = await fetch(BASE + path, init)
  } catch {
    throw new ApiError(0, ['Network error: could not reach the server'])
  }
  if (!res.ok) throw await toApiError(res)
  return res
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await send(path, init)
  return (await res.json()) as T
}

const jsonPost = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

const enc = encodeURIComponent

export const getConfig = () => request<Config>('/config')
export const getSamples = () => request<Sample[]>('/samples')
export const createRun = (req: RunRequest) =>
  request<{ run_id: string }>('/runs', jsonPost(req))
export const listRuns = () => request<RunSummary[]>('/runs')
export const getActiveRun = () => request<{ run_id: string | null }>('/runs/active')
export const getRun = (id: string) => request<Run>(`/runs/${enc(id)}`)
export const getHistory = (id: string) =>
  request<RunHistory>(`/runs/${enc(id)}/history`)
export const cancelRun = async (id: string): Promise<void> => {
  await send(`/runs/${enc(id)}/cancel`, jsonPost({}))
}

export function renderUrl(
  runId: string,
  plan: 'model' | 'packer',
  box: number,
  upto?: number,
): string {
  const q = `plan=${plan}&box=${box}` + (upto != null ? `&upto=${upto}` : '')
  return `${BASE}/runs/${enc(runId)}/render?${q}`
}

export function svgDownloadUrl(runId: string, plan: 'model' | 'packer'): string {
  return `${BASE}/runs/${enc(runId)}/svg?plan=${plan}`
}

export const isTerminal = (s: RunStatus) => s !== 'running'

export const EVENT_TYPES: RunEvent['type'][] = [
  'started',
  'extracted',
  'prepared',
  'packer',
  'round',
  'chosen',
  'end',
]

const ERROR_REFETCH_MS = 2000

/**
 * Subscribe to a run's SSE stream. Returns an unsubscribe function (does not
 * call onClosed). `onClosed` fires exactly once, after `end` or after the
 * error fallback found a terminal run; its argument is the refetched run
 * (null if that refetch failed).
 */
export function subscribe(
  runId: string,
  onEvent: (e: RunEvent) => void,
  onClosed: (run: Run | null) => void,
  fromId?: number,
): () => void {
  const url = `${BASE}/runs/${enc(runId)}/events` + (fromId != null ? `?from=${fromId}` : '')
  const es = new EventSource(url)
  const seen = new Set<number>()
  let closed = false
  let finished = false
  let lastErrorFetch = -Infinity
  let retryTimer: ReturnType<typeof setInterval> | null = null

  const stopRetry = () => {
    if (retryTimer != null) clearInterval(retryTimer)
    retryTimer = null
  }
  const close = () => {
    closed = true
    stopRetry()
    es.close()
  }
  const deliver = (e: RunEvent) => {
    if (seen.has(e.id)) return
    seen.add(e.id)
    onEvent(e)
  }
  const finish = (run: Run | null) => {
    if (finished) return
    finished = true
    onClosed(run)
  }

  for (const type of EVENT_TYPES) {
    es.addEventListener(type, (msg) => {
      if (closed) return
      let ev: RunEvent
      try {
        ev = JSON.parse((msg as MessageEvent).data) as RunEvent
      } catch {
        return
      }
      deliver(ev)
      if (type === 'end') {
        close()
        getRun(runId).then(finish, () => finish(null))
      }
    })
  }

  const startRetry = () => {
    if (retryTimer != null) return
    retryTimer = setInterval(() => {
      getRun(runId).then(
        (run) => {
          if (closed || finished || !isTerminal(run.status)) return
          close()
          for (const e of run.events) deliver(e)
          finish(run)
        },
        () => {
          /* backend still down; keep polling */
        },
      )
    }, ERROR_REFETCH_MS)
  }

  es.addEventListener('error', () => {
    if (closed) return
    if (es.readyState === EventSource.CLOSED && !finished) startRetry()
    const now = Date.now()
    if (now - lastErrorFetch < ERROR_REFETCH_MS) return
    lastErrorFetch = now
    getRun(runId).then(
      (run) => {
        if (closed || !isTerminal(run.status)) return
        close()
        for (const e of run.events) deliver(e)
        finish(run)
      },
      () => {
        /* keep waiting; EventSource retries on its own */
      },
    )
  })

  return () => {
    closed = true
    stopRetry()
    es.close()
  }
}
