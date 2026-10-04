import { BASE, enc, jsonPost, jsonPut, request, send, subscribe } from './client'
import type { ChatMessage } from './types'
import {
  EVENT_TYPES_V2,
  type ConfigV2,
  type EvalGroup,
  type Feedback,
  type FeedbackIn,
  type JobName,
  type RunEventV2,
  type RunRequestV2,
  type RunSummaryV2,
  type RunV2,
  type SampleV2,
} from './typesV2'

const V2 = '/v2'

export const getConfigV2 = () => request<ConfigV2>(`${V2}/config`)
export const getSamplesV2 = () => request<SampleV2[]>(`${V2}/samples`)
export const createRunV2 = (req: RunRequestV2) =>
  request<{ run_id: string }>(`${V2}/runs`, jsonPost(req))
export const listRunsV2 = () => request<RunSummaryV2[]>(`${V2}/runs`)
export const getActiveRunV2 = () => request<{ run_id: string | null }>(`${V2}/runs/active`)
export const getRunV2 = (id: string) => request<RunV2>(`${V2}/runs/${enc(id)}`)
export const cancelRunV2 = async (id: string): Promise<void> => {
  await send(`${V2}/runs/${enc(id)}/cancel`, jsonPost({}))
}

export type PlanKeyV2 = 'final' | 'model' | 'packer'

export function renderUrlV2(runId: string, plan: PlanKeyV2, box: number, upto?: number): string {
  const q = `plan=${plan}&box=${box}` + (upto != null ? `&upto=${upto}` : '')
  return `${BASE}${V2}/runs/${enc(runId)}/render?${q}`
}

export function svgUrlV2(runId: string, plan: PlanKeyV2): string {
  return `${BASE}${V2}/runs/${enc(runId)}/svg?plan=${plan}`
}

export const getJobMessages = (runId: string, job: JobName) =>
  request<{ messages: ChatMessage[] }>(`${V2}/runs/${enc(runId)}/jobs/${enc(job)}/messages`)

export const putFeedback = (runId: string, job: JobName, body: FeedbackIn) =>
  request<Feedback>(`${V2}/runs/${enc(runId)}/feedback/${enc(job)}`, jsonPut(body))

export type EvalQuery = { job?: JobName; since?: string; includeFake?: boolean }

export function getEvalSummary(q: EvalQuery = {}) {
  const p = new URLSearchParams()
  if (q.job) p.set('job', q.job)
  if (q.since) p.set('since', q.since)
  p.set('include_fake', q.includeFake ? 'true' : 'false')
  return request<{ groups: EvalGroup[] }>(`${V2}/eval/summary?${p.toString()}`)
}

export const subscribeV2 = (
  runId: string,
  onEvent: (e: RunEventV2) => void,
  onClosed: (run: RunV2 | null) => void,
  fromId?: number,
) =>
  subscribe<RunV2, RunEventV2>(runId, onEvent, onClosed, fromId, {
    base: `${BASE}${V2}`,
    getRun: getRunV2,
    types: EVENT_TYPES_V2,
  })

/** The run-flow adapter for useRunFlow. */
export const v2Api = {
  getRun: getRunV2,
  subscribe: subscribeV2,
  cancelRun: cancelRunV2,
}
