import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FakeEventSource } from '../test/fakeEventSource'
import { makeRun } from '../test/fixtures'
import {
  ApiError,
  cancelRun,
  createRun,
  getRun,
  renderUrl,
  subscribe,
  svgDownloadUrl,
} from './client'
import type { Run, RunEvent } from './types'

const json = (body: unknown, status = 200, statusText = 'OK') =>
  new Response(JSON.stringify(body), {
    status,
    statusText,
    headers: { 'Content-Type': 'application/json' },
  })

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  vi.useRealTimers()
})

describe('urls', () => {
  it('builds render and svg urls', () => {
    expect(renderUrl('abc', 'model', 2, 3)).toBe('/api/runs/abc/render?plan=model&box=2&upto=3')
    expect(renderUrl('abc', 'packer', 1)).toBe('/api/runs/abc/render?plan=packer&box=1')
    expect(svgDownloadUrl('abc', 'packer')).toBe('/api/runs/abc/svg?plan=packer')
  })
})

describe('requests', () => {
  it('createRun posts JSON and returns the run id', async () => {
    fetchMock.mockResolvedValue(json({ run_id: 'r1' }, 202))
    const req = {
      items: [],
      boxes: [],
      instructions: '',
      extraction: { model: 'm' },
      planning: { model: 'p', reasoning_effort: 'low' as const, start: 'improve' as const },
    }
    await expect(createRun(req)).resolves.toEqual({ run_id: 'r1' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/runs')
    expect(init.method).toBe('POST')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(init.body)).toEqual(req)
  })

  it('cancelRun posts an empty JSON object', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 202 }))
    await cancelRun('r1')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/runs/r1/cancel')
    expect(init.method).toBe('POST')
    expect(init.body).toBe('{}')
  })
})

describe('ApiError detail normalization', () => {
  const failWith = async (res: Response) => {
    fetchMock.mockResolvedValue(res)
    return getRun('x').then(
      () => {
        throw new Error('expected rejection')
      },
      (e: ApiError) => e,
    )
  }

  it('string detail', async () => {
    const e = await failWith(json({ detail: 'A run is already active' }, 409, 'Conflict'))
    expect(e).toBeInstanceOf(ApiError)
    expect(e.status).toBe(409)
    expect(e.messages).toEqual(['A run is already active'])
  })

  it('array detail drops the first loc part and the "Value error, " prefix', async () => {
    const e = await failWith(
      json(
        {
          detail: [
            { loc: ['body', 'items', 0, 'name'], msg: 'Value error, name must be unique', type: 'value_error' },
            { loc: ['body', 'instructions'], msg: 'String should have at most 2000 characters', type: 'x' },
          ],
        },
        422,
        'Unprocessable Entity',
      ),
    )
    expect(e.status).toBe(422)
    expect(e.messages).toEqual([
      'items.0.name: name must be unique',
      'instructions: String should have at most 2000 characters',
    ])
  })

  it('non-JSON body falls back to "<status> <statusText>"', async () => {
    const e = await failWith(new Response('Invalid host header', { status: 400, statusText: 'Bad Request' }))
    expect(e.status).toBe(400)
    expect(e.messages).toEqual(['400 Bad Request'])
  })
})

describe('subscribe', () => {
  const ev = (id: number, type: RunEvent['type'] = 'started'): RunEvent =>
    (type === 'end'
      ? { id, type, t: id, status: 'succeeded', error: null }
      : { id, type: 'started', t: id }) as RunEvent

  it('registers all 7 named listeners and uses ?from= when given', () => {
    subscribe('r1', vi.fn(), vi.fn(), 4)
    const es = FakeEventSource.last
    expect(es.url).toBe('/api/runs/r1/events?from=4')
    expect(es.listenerTypes()).toEqual(
      expect.arrayContaining(['started', 'extracted', 'prepared', 'packer', 'round', 'chosen', 'end']),
    )
  })

  it('omits ?from= when not attaching', () => {
    subscribe('r1', vi.fn(), vi.fn())
    expect(FakeEventSource.last.url).toBe('/api/runs/r1/events')
  })

  it('dedupes by id', () => {
    const onEvent = vi.fn()
    subscribe('r1', onEvent, vi.fn())
    const es = FakeEventSource.last
    es.emit('started', ev(0), 0)
    es.emit('started', ev(0), 0)
    es.emit('started', ev(1), 1)
    expect(onEvent).toHaveBeenCalledTimes(2)
  })

  it('closes on end, refetches the run, and calls onClosed exactly once', async () => {
    const run = makeRun()
    fetchMock.mockResolvedValue(json(run))
    const onEvent = vi.fn()
    const onClosed = vi.fn()
    subscribe('r1', onEvent, onClosed)
    const es = FakeEventSource.last
    es.emit('end', ev(3, 'end'), 3)
    expect(es.readyState).toBe(FakeEventSource.CLOSED)
    await vi.waitFor(() => expect(onClosed).toHaveBeenCalledTimes(1))
    expect(onClosed).toHaveBeenCalledWith(run)
    es.fail() // closed already: no further work
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('on error refetches the run; terminal -> delivers missing events, closes, onClosed once', async () => {
    const run: Run = makeRun({
      status: 'failed',
      events: [ev(0), ev(1), ev(2, 'end')],
    })
    fetchMock.mockResolvedValue(json(run))
    const onEvent = vi.fn()
    const onClosed = vi.fn()
    subscribe('r1', onEvent, onClosed)
    const es = FakeEventSource.last
    es.emit('started', ev(0), 0)
    es.fail()
    await vi.waitFor(() => expect(onClosed).toHaveBeenCalledTimes(1))
    expect(es.readyState).toBe(FakeEventSource.CLOSED)
    expect(onEvent.mock.calls.map((c) => c[0].id)).toEqual([0, 1, 2])
  })

  it('on error with a running run keeps the stream open', async () => {
    fetchMock.mockResolvedValue(json(makeRun({ status: 'running', events: [] })))
    const onClosed = vi.fn()
    subscribe('r1', vi.fn(), onClosed)
    const es = FakeEventSource.last
    es.fail()
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    await Promise.resolve()
    expect(es.readyState).not.toBe(FakeEventSource.CLOSED)
    expect(onClosed).not.toHaveBeenCalled()
  })

  it('refetches at most once per 2 s on repeated errors', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(async () => json(makeRun({ status: 'running', events: [] })))
    subscribe('r1', vi.fn(), vi.fn())
    const es = FakeEventSource.last
    es.fail()
    es.fail()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(2100)
    es.fail()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('unsubscribe closes the source without calling onClosed', () => {
    const onClosed = vi.fn()
    const off = subscribe('r1', vi.fn(), onClosed)
    off()
    expect(FakeEventSource.last.readyState).toBe(FakeEventSource.CLOSED)
    expect(onClosed).not.toHaveBeenCalled()
  })

  describe('closed-source polling', () => {
    const failClosed = (es: FakeEventSource) => {
      es.readyState = FakeEventSource.CLOSED
      for (const l of es.listeners.get('error') ?? []) l(new Event('error') as MessageEvent)
    }

    it('polls after CLOSED error until a terminal run is found', async () => {
      vi.useFakeTimers()
      const run = makeRun({
        status: 'interrupted',
        events: [ev(0), ev(1, 'end')],
      })
      fetchMock
        .mockRejectedValueOnce(new TypeError('down'))
        .mockResolvedValue(json(run))
      const onEvent = vi.fn()
      const onClosed = vi.fn()
      subscribe('r1', onEvent, onClosed)
      const es = FakeEventSource.last
      es.emit('started', ev(0), 0)
      failClosed(es)
      failClosed(es) // must not start a second timer
      await vi.advanceTimersByTimeAsync(0) // immediate error-handler getRun rejects
      expect(onClosed).not.toHaveBeenCalled()
      await vi.advanceTimersByTimeAsync(2000) // timer getRun succeeds
      expect(onClosed).toHaveBeenCalledTimes(1)
      expect(onClosed.mock.calls[0][0]).toMatchObject({ status: 'interrupted' })
      expect(onEvent.mock.calls.map((c) => c[0].id)).toEqual([0, 1])
      const calls = fetchMock.mock.calls.length
      expect(calls).toBe(2)
      await vi.advanceTimersByTimeAsync(10000)
      expect(fetchMock).toHaveBeenCalledTimes(calls)
      expect(onClosed).toHaveBeenCalledTimes(1)
    })

    it('unsubscribe stops polling', async () => {
      vi.useFakeTimers()
      fetchMock.mockRejectedValue(new TypeError('down'))
      const off = subscribe('r1', vi.fn(), vi.fn())
      failClosed(FakeEventSource.last)
      await vi.advanceTimersByTimeAsync(2000)
      expect(fetchMock).toHaveBeenCalledTimes(2) // immediate + one timer tick
      off()
      await vi.advanceTimersByTimeAsync(10000)
      expect(fetchMock).toHaveBeenCalledTimes(2)
    })
  })
})
