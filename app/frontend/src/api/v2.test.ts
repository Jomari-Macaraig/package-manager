import { afterEach, describe, expect, it, vi } from 'vitest'
import { FakeEventSource } from '../test/fakeEventSource'
import { makeRun } from '../test/fixtures'
import { jsonResponse, mockFetch, requests } from '../test/mockFetch'
import { makeRunV2, RUN_V2_ID } from '../test/fixturesV2'
import { ApiError, createRun, subscribe } from './client'
import {
  cancelRunV2,
  createRunV2,
  getActiveRunV2,
  getEvalSummary,
  getJobMessages,
  getRunV2,
  putFeedback,
  renderUrlV2,
  subscribeV2,
  svgUrlV2,
} from './v2'
import type { Run, RunEvent } from './types'
import type { RunEventV2, RunV2 } from './typesV2'

afterEach(() => {
  vi.useRealTimers()
})

describe('v2 urls', () => {
  it('builds render and svg urls with plan=final|model|packer', () => {
    expect(renderUrlV2('abc', 'final', 2, 3)).toBe('/api/v2/runs/abc/render?plan=final&box=2&upto=3')
    expect(renderUrlV2('abc', 'packer', 1)).toBe('/api/v2/runs/abc/render?plan=packer&box=1')
    expect(svgUrlV2('abc', 'model')).toBe('/api/v2/runs/abc/svg?plan=model')
  })
})

describe('v2 requests', () => {
  it('putFeedback sends PUT with the body to /feedback/{job}', async () => {
    const f = mockFetch({
      'PUT /api/v2/runs/r1/feedback/check': {
        rating: 'up',
        comment: 'c',
        wrong_verdicts: [1],
        updated_at: 'x',
      },
    })
    const fb = await putFeedback('r1', 'check', { rating: 'up', comment: 'c', wrong_verdicts: [1] })
    expect(fb.wrong_verdicts).toEqual([1])
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/v2/runs/r1/feedback/check')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({ rating: 'up', comment: 'c', wrong_verdicts: [1] })
  })

  it('posts runs and cancels, reads active, run and messages', async () => {
    const f = mockFetch({
      'POST /api/v2/runs': { run_id: 'r1' },
      'POST /api/v2/runs/r1/cancel': {},
      'GET /api/v2/runs/active': { run_id: null },
      'GET /api/v2/runs/r1': makeRunV2(),
      'GET /api/v2/runs/r1/jobs/notes/messages': { messages: [{ role: 'user', content: 'hi' }] },
    })
    await expect(createRunV2({ items: [], boxes: [], instructions: '' })).resolves.toEqual({
      run_id: 'r1',
    })
    await cancelRunV2('r1')
    await getActiveRunV2()
    expect((await getRunV2('r1')).run_id).toBe(RUN_V2_ID)
    expect((await getJobMessages('r1', 'notes')).messages).toHaveLength(1)
    expect(requests(f)).toEqual([
      'POST /api/v2/runs',
      'POST /api/v2/runs/r1/cancel',
      'GET /api/v2/runs/active',
      'GET /api/v2/runs/r1',
      'GET /api/v2/runs/r1/jobs/notes/messages',
    ])
  })

  it('getEvalSummary sends job, since and include_fake', async () => {
    const f = mockFetch({ 'GET /api/v2/eval/summary': { groups: [] } })
    await getEvalSummary({ job: 'check', since: '2026-01-01T00:00:00Z', includeFake: true })
    await getEvalSummary()
    expect(requests(f)).toEqual([
      'GET /api/v2/eval/summary?job=check&since=2026-01-01T00%3A00%3A00Z&include_fake=true',
      'GET /api/v2/eval/summary?include_fake=false',
    ])
  })

  it('an inline 422 keeps its normalized messages', async () => {
    mockFetch({
      'PUT /api/v2/runs/r1/feedback/strategy': new Response(
        JSON.stringify({ detail: 'job was skipped' }),
        { status: 422, headers: { 'Content-Type': 'application/json' } },
      ),
    })
    await expect(putFeedback('r1', 'strategy', {})).rejects.toMatchObject({
      status: 422,
      messages: ['job was skipped'],
    })
  })

  it('a 409 carries the body kind; v1 default has none', async () => {
    mockFetch({
      'POST /api/v2/runs': jsonResponse({ detail: 'A run is already in progress', kind: 'v1' }, 409),
      'POST /api/runs': jsonResponse({ detail: 'A run is already in progress' }, 409),
    })
    const e = await createRunV2({ items: [], boxes: [], instructions: '' }).catch((x) => x)
    expect(e).toBeInstanceOf(ApiError)
    expect(e.status).toBe(409)
    expect(e.kind).toBe('v1')
    const v1 = await createRun(makeRun().request).catch((x) => x)
    expect(v1.kind).toBeNull()
    expect(v1.messages).toEqual(['A run is already in progress'])
  })
})

describe('subscribe options', () => {
  it('defaults: /api base and v1 event names', () => {
    subscribe('r1', () => {}, () => {}, 3)
    const es = FakeEventSource.last
    expect(es.url).toBe('/api/runs/r1/events?from=3')
    expect(es.listenerTypes()).toContain('round')
    expect(es.listenerTypes()).not.toContain('job')
  })

  it('base and types switch the URL and the listened events', () => {
    const got: RunEventV2[] = []
    subscribeV2('r1', (e) => got.push(e), () => {}, 0)
    const es = FakeEventSource.last
    expect(es.url).toBe('/api/v2/runs/r1/events?from=0')
    expect(es.listenerTypes().sort()).toEqual(['chosen', 'end', 'error', 'job', 'packer', 'started'])
    es.emit('job', { id: 1, t: 1, type: 'job', job: 'extract', status: 'ok', passed: 2, total: 2 })
    expect(got).toHaveLength(1)
  })

  it('getRun option is used for the end refetch', async () => {
    const getRunFn = vi.fn().mockResolvedValue(makeRunV2())
    const closed = vi.fn()
    subscribe<RunV2, RunEventV2>('r1', () => {}, closed, undefined, {
      base: '/api/v2',
      getRun: getRunFn,
      types: ['started', 'end'],
    })
    FakeEventSource.last.emit('end', { id: 5, t: 5, type: 'end', status: 'succeeded', error: null })
    await vi.waitFor(() => expect(closed).toHaveBeenCalledTimes(1))
    expect(getRunFn).toHaveBeenCalledWith('r1')
    expect(closed.mock.calls[0][0].run_id).toBe(RUN_V2_ID)
  })

  it('getRun option is used for the error polling fallback', async () => {
    vi.useFakeTimers()
    const getRunFn = vi.fn().mockResolvedValue(makeRunV2())
    const closed = vi.fn()
    const got: RunEventV2[] = []
    subscribe<RunV2, RunEventV2>('r1', (e) => got.push(e), closed, undefined, {
      base: '/api/v2',
      getRun: getRunFn,
      types: ['started', 'end'],
    })
    FakeEventSource.last.fail()
    await vi.advanceTimersByTimeAsync(10)
    expect(getRunFn).toHaveBeenCalled()
    expect(closed).toHaveBeenCalledTimes(1)
    expect(got.length).toBe(makeRunV2().events.length)
  })

  it('without options the v1 getRun (fetch /api/runs/{id}) is used', async () => {
    const f = mockFetch({ 'GET /api/runs/r1': makeRun() })
    const closed = vi.fn<(r: Run | null) => void>()
    subscribe<Run, RunEvent>('r1', () => {}, closed)
    FakeEventSource.last.emit('end', { id: 6, type: 'end', t: 6, status: 'succeeded', error: null })
    await vi.waitFor(() => expect(closed).toHaveBeenCalledTimes(1))
    expect(requests(f)).toEqual(['GET /api/runs/r1'])
  })
})
