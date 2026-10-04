import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { FakeEventSource } from './test/fakeEventSource'
import { config, events, makeRun, samples, summaries } from './test/fixtures'
import { configV2, eventsV2, makeRunV2, samplesV2, summariesV2 } from './test/fixturesV2'
import { jsonResponse, mockFetch, requests, type Responder } from './test/mockFetch'

const PIPELINE_KEY = 'packing-playground.pipeline'

const base = (): Record<string, Responder> => ({
  'GET /api/config': config,
  'GET /api/samples': samples,
  'GET /api/runs': summaries,
  'GET /api/runs/active': { run_id: null },
  'GET /api/v2/config': configV2,
  'GET /api/v2/samples': samplesV2,
  'GET /api/v2/runs': summariesV2,
  'GET /api/v2/runs/active': { run_id: null },
  'GET /api/v2/eval/summary': { groups: [] },
})

let f: ReturnType<typeof mockFetch>
const setup = (extra: Record<string, Responder> = {}) => {
  f = mockFetch({ ...base(), ...extra })
}

async function ready() {
  const user = userEvent.setup()
  render(<App />)
  await screen.findByLabelText('Item 1 name')
  return user
}

const pressed = (name: string) => screen.getByRole('button', { name })

// Most tests start from v1 and switch; the default itself is tested below.
beforeEach(() => {
  localStorage.setItem(PIPELINE_KEY, 'v1')
  setup()
})

describe('pipeline switch', () => {
  it('v2 is the default when nothing is saved', async () => {
    localStorage.removeItem(PIPELINE_KEY)
    await ready()
    expect(pressed('v2')).toHaveAttribute('aria-pressed', 'true')
    expect(pressed('v1')).toHaveAttribute('aria-pressed', 'false')
    expect(await screen.findByLabelText('Strategy model')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Planning model/)).not.toBeInTheDocument()
  })

  it('a saved v1 choice is kept, pressed state is exposed, and v2 is not loaded yet', async () => {
    await ready()
    expect(pressed('v1')).toHaveAttribute('aria-pressed', 'true')
    expect(pressed('v2')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByLabelText(/Planning model/)).toBeInTheDocument()
    expect(requests(f)).not.toContain('GET /api/v2/config')
    expect(requests(f)).not.toContain('GET /api/v2/samples')
  })

  it('selecting v2 loads its config and samples once, shows JobSettings and keeps the editor', async () => {
    const user = await ready()
    await user.click(pressed('v2'))
    expect(await screen.findByLabelText('Strategy model')).toBeInTheDocument()
    expect(pressed('v2')).toHaveAttribute('aria-pressed', 'true')
    expect(pressed('v1')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByLabelText(/Planning model/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Item 1 name')).toHaveValue('Mug')
    await user.click(pressed('v1'))
    await user.click(pressed('v2'))
    expect(requests(f).filter((r) => r === 'GET /api/v2/config')).toHaveLength(1)
    expect(localStorage.getItem(PIPELINE_KEY)).toBe('v2')
  })

  it('v2 picker lists the v2 samples (A–H) and v1 the v1 samples', async () => {
    const user = await ready()
    expect(screen.queryByRole('option', { name: /Sample G/ })).not.toBeInTheDocument()
    await user.click(pressed('v2'))
    expect(await screen.findByRole('option', { name: /Sample G/ })).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Sample order'), 'G')
    await user.click(screen.getByRole('button', { name: 'Load' }))
    expect(screen.getByLabelText('Packing instructions')).toHaveValue(
      'Keep the jars on the left side and the kettles on the right.',
    )
  })

  it('restores a saved v2 choice', async () => {
    localStorage.setItem(PIPELINE_KEY, 'v2')
    await ready()
    expect(pressed('v2')).toHaveAttribute('aria-pressed', 'true')
    expect(await screen.findByLabelText('Strategy model')).toBeInTheDocument()
  })

  it('still works when localStorage throws', async () => {
    const broken = {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
      clear: () => {},
    }
    vi.stubGlobal('localStorage', broken)
    const user = await ready()
    expect(pressed('v2')).toHaveAttribute('aria-pressed', 'true')
    expect(await screen.findByLabelText('Strategy model')).toBeInTheDocument()
    await user.click(pressed('v1'))
    expect(pressed('v1')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText(/Planning model/)).toBeInTheDocument()
  })

  it('a v2 run is posted with all four jobs and attached on /api/v2', async () => {
    const running = makeRunV2({ status: 'running', finished_at: null, events: [eventsV2[0]], jobs: [] })
    setup({
      'POST /api/v2/runs': { run_id: running.run_id },
      [`GET /api/v2/runs/${running.run_id}`]: running,
    })
    const user = await ready()
    await user.click(pressed('v2'))
    await screen.findByLabelText('Strategy model')
    await user.selectOptions(screen.getByLabelText('Strategy effort'), 'high')
    await user.click(screen.getByRole('button', { name: 'Pack' }))
    await waitFor(() =>
      expect(FakeEventSource.instances.some((e) => e.url.startsWith('/api/v2/runs/'))).toBe(true),
    )
    const post = f.mock.calls.find(([u, i]) => u === '/api/v2/runs' && i?.method === 'POST')!
    const body = JSON.parse(String(post[1]?.body))
    expect(Object.keys(body.jobs).sort()).toEqual(['check', 'extract', 'notes', 'strategy'])
    expect(body.jobs.strategy).toEqual({ model: 'gpt-5.4-nano', reasoning_effort: 'high' })
    expect(body.jobs.extract).toEqual({ model: 'gpt-4.1-nano', reasoning_effort: null })
    expect(body.items).toHaveLength(5)
    expect(FakeEventSource.last.url).toBe(`/api/v2/runs/${running.run_id}/events?from=1`)
    expect(screen.getByText('Run started')).toBeInTheDocument()
    // live job events render as progress and in the log
    act(() =>
      FakeEventSource.last.emit('job', {
        id: 1,
        t: 1,
        type: 'job',
        job: 'extract',
        status: 'ok',
        passed: 2,
        total: 2,
      }),
    )
    expect(screen.getAllByText(/Job extract: ok \(checks 2\/2 passed\)/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/may take up to ~45 s/)).not.toBeInTheDocument()
  })
})

describe('tabs', () => {
  it('Playground | Evaluation use tab roles, and the editor stays mounted', async () => {
    const user = await ready()
    const tabs = screen.getByRole('tablist')
    const play = within(tabs).getByRole('tab', { name: 'Playground' })
    const evalTab = within(tabs).getByRole('tab', { name: 'Evaluation' })
    expect(play).toHaveAttribute('aria-selected', 'true')
    expect(play).toHaveAttribute('aria-controls', 'panel-playground')
    expect(evalTab).toHaveAttribute('aria-selected', 'false')
    expect(evalTab).toHaveAttribute('aria-controls', 'panel-evaluation')
    await user.click(evalTab)
    expect(evalTab).toHaveAttribute('aria-selected', 'true')
    expect(play).toHaveAttribute('aria-selected', 'false')
    const evalPanel = document.getElementById('panel-evaluation')!
    expect(evalPanel).toHaveAttribute('role', 'tabpanel')
    expect(evalPanel).toHaveAttribute('aria-labelledby', 'tab-evaluation')
    expect(evalPanel).not.toHaveAttribute('hidden')
    const playPanel = document.getElementById('panel-playground')!
    expect(playPanel).toHaveAttribute('hidden')
    // the editor is still mounted, with its state
    expect(within(playPanel).getByLabelText('Item 1 name')).toHaveValue('Mug')
    expect(await screen.findByText('No v2 runs yet')).toBeInTheDocument()
    await user.click(play)
    expect(playPanel).not.toHaveAttribute('hidden')
    expect(evalPanel).toHaveAttribute('hidden')
  })

  it('arrow keys move between tabs', async () => {
    const user = await ready()
    pressedTab('Playground').focus()
    await user.keyboard('{ArrowRight}')
    expect(pressedTab('Evaluation')).toHaveAttribute('aria-selected', 'true')
    expect(pressedTab('Evaluation')).toHaveFocus()
  })
})

const pressedTab = (name: string) => screen.getByRole('tab', { name })

describe('the 409 second line', () => {
  it('409 from a v1 POST shows the second line and Show it attaches to the v2 run', async () => {
    const live = makeRunV2({ status: 'running', finished_at: null, events: eventsV2.slice(0, 2), jobs: [] })
    let activeV2: string | null = null
    setup({
      'POST /api/runs': jsonResponse({ detail: 'A run is already in progress', kind: 'v2' }, 409),
      'GET /api/v2/runs/active': () => ({ run_id: activeV2 }),
      [`GET /api/v2/runs/${live.run_id}`]: live,
    })
    const user = await ready()
    await user.click(screen.getByRole('button', { name: 'Pack' }))
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText('A run is already in progress')).toBeInTheDocument()
    expect(within(alert).getByText('(a v2 run)')).toBeInTheDocument()
    activeV2 = live.run_id
    await user.click(screen.getByRole('button', { name: 'Show it' }))
    await waitFor(() => expect(pressed('v2')).toHaveAttribute('aria-pressed', 'true'))
    await waitFor(() =>
      expect(FakeEventSource.last.url).toBe(`/api/v2/runs/${live.run_id}/events?from=2`),
    )
    expect(screen.queryByText('(a v2 run)')).not.toBeInTheDocument()
  })

  it('a 409 without kind has no second line', async () => {
    setup({ 'POST /api/runs': jsonResponse({ detail: 'A run is already in progress' }, 409) })
    const user = await ready()
    await user.click(screen.getByRole('button', { name: 'Pack' }))
    expect(await screen.findByText('A run is already in progress')).toBeInTheDocument()
    expect(screen.queryByText(/^\(a v\d run\)$/)).not.toBeInTheDocument()
  })
})

describe('active-run check on load', () => {
  it('a v2 failure is silent: no alert and no error state', async () => {
    setup({ 'GET /api/v2/runs/active': jsonResponse({ detail: 'boom' }, 500) })
    await ready()
    await waitFor(() => expect(requests(f)).toContain('GET /api/v2/runs/active'))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('the v2 check runs when v1 reports none, and attaches (switching to v2)', async () => {
    const live = makeRunV2({ status: 'running', finished_at: null, events: eventsV2.slice(0, 3), jobs: [] })
    setup({
      'GET /api/v2/runs/active': { run_id: live.run_id },
      [`GET /api/v2/runs/${live.run_id}`]: live,
    })
    await ready()
    await waitFor(() =>
      expect(FakeEventSource.last?.url).toBe(`/api/v2/runs/${live.run_id}/events?from=3`),
    )
    expect(pressed('v2')).toHaveAttribute('aria-pressed', 'true')
    // not persisted: the user did not choose it (the saved choice stays v1)
    expect(localStorage.getItem(PIPELINE_KEY)).toBe('v1')
  })

  it('the v2 check is skipped when v1 has an active run', async () => {
    const live = makeRun({ run_id: 'live', status: 'running', events: events.slice(0, 2) })
    setup({ 'GET /api/runs/active': { run_id: 'live' }, 'GET /api/runs/live': live })
    await ready()
    await waitFor(() => expect(FakeEventSource.last?.url).toBe('/api/runs/live/events?from=2'))
    expect(requests(f)).not.toContain('GET /api/v2/runs/active')
  })

  it('with v2 selected, v2 is checked straight away', async () => {
    localStorage.setItem(PIPELINE_KEY, 'v2')
    await ready()
    expect(requests(f)).toContain('GET /api/v2/runs/active')
  })
})

describe('two flows', () => {
  it('a running v1 run disables Pack under v2 too, and each pipeline shows only its own controls', async () => {
    const live = makeRun({ run_id: 'live', status: 'running', events: events.slice(0, 2) })
    setup({ 'GET /api/runs/active': { run_id: 'live' }, 'GET /api/runs/live': live })
    const user = await ready()
    await screen.findByRole('button', { name: 'Cancel' })
    expect(screen.getByRole('button', { name: 'Pack' })).toBeDisabled()
    await user.click(pressed('v2'))
    await screen.findByLabelText('Strategy model')
    expect(screen.getByRole('button', { name: 'Pack' })).toBeDisabled()
    // the v2 flow has nothing running: no Cancel, no v1 events in its log
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Settings extracted/)).not.toBeInTheDocument()
    await user.click(pressed('v1'))
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(screen.getByText(/Settings extracted/)).toBeInTheDocument()
  })

  it('a v2 run finishing refreshes the v2 history only', async () => {
    const running = makeRunV2({ status: 'running', finished_at: null, events: [eventsV2[0]], jobs: [] })
    setup({
      'POST /api/v2/runs': { run_id: running.run_id },
      [`GET /api/v2/runs/${running.run_id}`]: running,
    })
    const user = await ready()
    await user.click(pressed('v2'))
    await screen.findByLabelText('Strategy model')
    await user.click(screen.getByRole('button', { name: 'Pack' }))
    await waitFor(() => expect(FakeEventSource.last.url).toContain('/api/v2/runs/'))
    const v1Lists = () => requests(f).filter((r) => r === 'GET /api/runs').length
    const v2Lists = () => requests(f).filter((r) => r === 'GET /api/v2/runs').length
    const [b1, b2] = [v1Lists(), v2Lists()]
    f.mockClear()
    setup({
      'GET /api/v2/runs': summariesV2,
      [`GET /api/v2/runs/${running.run_id}`]: makeRunV2(),
    })
    act(() =>
      FakeEventSource.last.emit('end', { id: 5, t: 5, type: 'end', status: 'succeeded', error: null }),
    )
    expect(await screen.findByText('Status: succeeded')).toBeInTheDocument()
    await waitFor(() => expect(requests(f)).toContain('GET /api/v2/runs'))
    expect(requests(f)).not.toContain('GET /api/runs')
    expect([b1, b2].every((n) => n >= 1)).toBe(true)
    // the finished run renders with its Jobs panel
    expect(screen.getAllByRole('article')).toHaveLength(4)
  })
})

describe('v2 history', () => {
  it('shows $cost · source · reason · over budget and opens a past v2 run read-only', async () => {
    setup({ [`GET /api/v2/runs/${summariesV2[0].run_id}`]: makeRunV2() })
    localStorage.setItem(PIPELINE_KEY, 'v2')
    const user = await ready()
    await user.click(screen.getByRole('button', { name: 'History' }))
    expect(
      await screen.findByText(
        'succeeded · $0.0027 · chosen: model · honours the instructions · over budget',
      ),
    ).toBeInTheDocument()
    await user.click(screen.getByText('Mug, Dinner Plate, Candle Jar'))
    expect(await screen.findByText('Viewing a past run (read-only)')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Jobs' })).toBeInTheDocument()
    expect(FakeEventSource.instances).toHaveLength(0)
  })
})
