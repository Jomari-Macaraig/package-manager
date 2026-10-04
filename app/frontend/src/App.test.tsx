import { render, screen, waitFor, within, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import * as client from './api/client'
import type { Run, RunEvent } from './api/types'
import { config, events, makeRun, samples, sampleB, summaries } from './test/fixtures'

vi.mock('./api/client', async (orig) => {
  const actual = await orig<typeof import('./api/client')>()
  return {
    ...actual,
    getConfig: vi.fn(),
    getSamples: vi.fn(),
    createRun: vi.fn(),
    listRuns: vi.fn(),
    getActiveRun: vi.fn(),
    getRun: vi.fn(),
    cancelRun: vi.fn(),
    subscribe: vi.fn(),
  }
})

const m = vi.mocked(client)

type Sub = {
  id: string
  onEvent: (e: RunEvent) => void
  onClosed: (r: Run | null) => void
  from?: number
  off: ReturnType<typeof vi.fn>
}
let subs: Sub[]

beforeEach(() => {
  localStorage.setItem('packing-playground.pipeline', 'v1')
  vi.resetAllMocks()
  subs = []
  m.getConfig.mockResolvedValue(config)
  m.getSamples.mockResolvedValue(samples)
  m.listRuns.mockResolvedValue(summaries)
  m.getActiveRun.mockResolvedValue({ run_id: null })
  m.subscribe.mockImplementation((id, onEvent, onClosed, from) => {
    const off = vi.fn()
    subs.push({ id, onEvent, onClosed, from, off })
    return off
  })
})

async function ready() {
  const user = userEvent.setup()
  render(<App />)
  await screen.findByLabelText('Item 1 name')
  return user
}

describe('editor', () => {
  it('preloads Sample A and loading Sample B fills the tables (6 items, 5 boxes)', async () => {
    const user = await ready()
    expect(screen.getByLabelText('Item 1 name')).toHaveValue('Mug')
    await user.selectOptions(screen.getByLabelText('Sample order'), 'B')
    await user.click(screen.getByRole('button', { name: 'Load' }))
    expect(screen.getAllByLabelText(/^Item \d+ name$/)).toHaveLength(6)
    expect(screen.getAllByLabelText(/^Box \d+ name$/)).toHaveLength(5)
    expect(screen.getByLabelText('Item 1 name')).toHaveValue('Monitor (boxed)')
    expect(screen.getByLabelText('Box 4 name')).toHaveValue('Monitor-28')
    expect(screen.getByTestId('unit-total')).toHaveTextContent('18 / 150 units')
  })

  it('asks for confirmation before replacing edits', async () => {
    const user = await ready()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await user.type(screen.getByLabelText('Item 1 color'), 'x')
    await user.selectOptions(screen.getByLabelText('Sample order'), 'B')
    await user.click(screen.getByRole('button', { name: 'Load' }))
    expect(confirm).toHaveBeenCalled()
    expect(screen.getByLabelText('Item 1 name')).toHaveValue('Mug')
    confirm.mockReturnValue(true)
    await user.click(screen.getByRole('button', { name: 'Load' }))
    expect(screen.getByLabelText('Item 1 name')).toHaveValue('Monitor (boxed)')
  })

  it('adds and removes rows', async () => {
    const user = await ready()
    await user.click(screen.getByRole('button', { name: 'Add item' }))
    expect(screen.getAllByLabelText(/^Item \d+ name$/)).toHaveLength(6)
    await user.click(screen.getByRole('button', { name: 'Remove item row 6' }))
    expect(screen.getAllByLabelText(/^Item \d+ name$/)).toHaveLength(5)
    await user.click(screen.getByRole('button', { name: 'Add box' }))
    expect(screen.getAllByLabelText(/^Box \d+ name$/)).toHaveLength(6)
  })

  it('duplicate names (case-insensitive) disable Pack', async () => {
    const user = await ready()
    const pack = screen.getByRole('button', { name: 'Pack' })
    expect(pack).toBeEnabled()
    const second = screen.getByLabelText('Item 2 name')
    await user.clear(second)
    await user.type(second, 'MUG')
    expect(pack).toBeDisabled()
    expect(screen.getByText(/name must be unique/)).toBeInTheDocument()
    expect(second).toHaveAttribute('aria-invalid', 'true')
  })

  it('an empty cell disables Pack and the unit cap is enforced', async () => {
    const user = await ready()
    await user.clear(screen.getByLabelText('Item 1 weight (lb)'))
    expect(screen.getByRole('button', { name: 'Pack' })).toBeDisabled()
    await user.type(screen.getByLabelText('Item 1 weight (lb)'), '1')
    expect(screen.getByRole('button', { name: 'Pack' })).toBeEnabled()
    const qty = screen.getByLabelText('Item 1 qty')
    await user.clear(qty)
    await user.type(qty, '200')
    expect(screen.getByRole('button', { name: 'Pack' })).toBeDisabled()
    expect(screen.getByTestId('unit-total')).toHaveTextContent('/ 150 units')
  })

  it('shows auto for empty or unsupported colors', async () => {
    const user = await ready()
    await user.clear(screen.getByLabelText('Item 1 color'))
    const row = screen.getByLabelText('Item 1 color').closest('td')!
    expect(within(row).getByText('auto')).toBeInTheDocument()
  })

  it('hides the effort select for models without efforts', async () => {
    const user = await ready()
    expect(screen.getByLabelText(/Reasoning effort/)).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText(/Planning model/), 'gpt-4.1-mini')
    expect(screen.queryByLabelText(/Reasoning effort/)).not.toBeInTheDocument()
    expect(screen.getByText('max rounds 3 · improve rounds 1')).toBeInTheDocument()
  })

  it('sends the planning start choice', async () => {
    m.createRun.mockResolvedValue({ run_id: 'r1' })
    const user = await ready()
    await user.selectOptions(screen.getByLabelText(/Planning start/), 'guide')
    await user.click(screen.getByRole('button', { name: 'Pack' }))
    expect(m.createRun.mock.calls[0][0].planning.start).toBe('guide')
  })

  it('shows a retry banner when the config cannot load', async () => {
    m.getConfig.mockRejectedValueOnce(new client.ApiError(500, ['boom']))
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByText(/Could not load configuration: boom/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pack' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByLabelText('Item 1 name')).toBeInTheDocument()
  })
})

describe('request body', () => {
  it('matches RunRequest: no ids, blank stock -> null, trimmed names, blank color -> null', async () => {
    m.createRun.mockResolvedValue({ run_id: 'r1' })
    m.getRun.mockResolvedValue(makeRun({ run_id: 'r1', status: 'running', events: [] }))
    const user = await ready()
    await user.selectOptions(screen.getByLabelText('Sample order'), 'B')
    await user.click(screen.getByRole('button', { name: 'Load' }))
    await user.clear(screen.getByLabelText('Item 6 color'))
    await user.type(screen.getByLabelText('Box 2 stock'), '3')
    await user.type(screen.getByLabelText('Packing instructions'), 'be careful')
    await user.click(screen.getByRole('button', { name: 'Pack' }))
    await waitFor(() => expect(m.createRun).toHaveBeenCalledTimes(1))
    const body = m.createRun.mock.calls[0][0]
    expect(body.items).toHaveLength(6)
    expect(body.boxes).toHaveLength(5)
    expect(body.items[0]).toEqual({
      name: 'Monitor (boxed)',
      color: 'black',
      weight: 14,
      x: 16,
      y: 6,
      z: 25,
      quantity: 1,
    })
    expect(body.items[5].color).toBeNull()
    expect(body.boxes[0]).toEqual({ name: 'M-12', weightMax: 50, x: 8, y: 12, z: 12, stock: null })
    expect(body.boxes[1].stock).toBe(3)
    expect(body.instructions).toBe('be careful')
    expect(body.extraction).toEqual({ model: 'gpt-4.1-nano' })
    expect(body.planning).toEqual({
      model: 'gpt-5.4-nano',
      reasoning_effort: 'low',
      start: 'improve',
    })
    expect(JSON.stringify(body)).not.toMatch(/"id"/)
    expect(sampleB.items).toHaveLength(6)
  })

  it('shows normalized 422 messages', async () => {
    m.createRun.mockRejectedValue(new client.ApiError(422, ['items.0.name: bad']))
    const user = await ready()
    await user.click(screen.getByRole('button', { name: 'Pack' }))
    expect(await screen.findByText('items.0.name: bad')).toBeInTheDocument()
  })
})

describe('run flow', () => {
  async function startRun() {
    m.createRun.mockResolvedValue({ run_id: 'r1' })
    m.getRun.mockResolvedValue(makeRun({ run_id: 'r1', status: 'running', events: [events[0]] }))
    const user = await ready()
    await user.click(screen.getByRole('button', { name: 'Pack' }))
    await waitFor(() => expect(subs).toHaveLength(1))
    return user
  }

  it('attaches with getRun first, seeds the log, and subscribes from lastId + 1', async () => {
    await startRun()
    expect(subs[0].id).toBe('r1')
    expect(subs[0].from).toBe(1)
    expect(screen.getByText('Run started')).toBeInTheDocument()
  })

  it('renders pushed events in an aria-live log and disables Pack while running', async () => {
    await startRun()
    const log = screen.getByRole('log', { name: 'Progress' })
    expect(log).toHaveAttribute('aria-live', 'polite')
    act(() => subs[0].onEvent(events[2]))
    expect(within(log).getByText(/Prepared 5 item types, 14 units/)).toBeInTheDocument()
    expect(within(log).getByText(/may take up to ~45 s/)).toBeInTheDocument()
    act(() => subs[0].onEvent(events[3]))
    act(() => subs[0].onEvent(events[4]))
    expect(within(log).getByText(/Round 2 \(fix\): invalid, 5 errors/)).toBeInTheDocument()
    expect(within(log).getByText(/Code packer: 2 boxes/)).toBeInTheDocument()
    expect(within(log).queryByText(/may take up to ~45 s/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pack' })).toBeDisabled()
  })

  it('ignores a duplicate event id', async () => {
    await startRun()
    act(() => subs[0].onEvent(events[1]))
    act(() => subs[0].onEvent(events[1]))
    expect(screen.getAllByText(/Settings extracted/)).toHaveLength(1)
  })

  it('Cancel posts and shows the cancelling state', async () => {
    m.cancelRun.mockResolvedValue()
    const user = await startRun()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(m.cancelRun).toHaveBeenCalledWith('r1')
    expect(
      screen.getByText('Cancelling… — takes effect before the next model call'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  })

  it('on close shows the final run, re-enables Pack, and refreshes history', async () => {
    await startRun()
    const before = m.listRuns.mock.calls.length
    act(() => subs[0].onClosed(makeRun({ run_id: 'r1' })))
    expect(await screen.findByText('Status: succeeded')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pack' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
    await waitFor(() => expect(m.listRuns.mock.calls.length).toBeGreaterThan(before))
  })

  it('409 shows "A run is already in progress" and Show it attaches to the active run', async () => {
    m.createRun.mockRejectedValue(new client.ApiError(409, ['A run is active']))
    m.getActiveRun.mockResolvedValue({ run_id: null })
    const user = await ready()
    await user.click(screen.getByRole('button', { name: 'Pack' }))
    expect(await screen.findByText('A run is already in progress')).toBeInTheDocument()
    m.getActiveRun.mockResolvedValue({ run_id: 'other' })
    m.getRun.mockResolvedValue(makeRun({ run_id: 'other', status: 'running', events: events.slice(0, 3) }))
    await user.click(screen.getByRole('button', { name: 'Show it' }))
    await waitFor(() => expect(subs).toHaveLength(1))
    expect(subs[0].id).toBe('other')
    expect(subs[0].from).toBe(3)
  })

  it('attaches to an active run on page load', async () => {
    m.getActiveRun.mockResolvedValue({ run_id: 'live' })
    m.getRun.mockResolvedValue(makeRun({ run_id: 'live', status: 'running', events: events.slice(0, 2) }))
    render(<App />)
    await waitFor(() => expect(subs).toHaveLength(1))
    expect(subs[0].id).toBe('live')
    expect(subs[0].from).toBe(2)
    expect(await screen.findByText(/Settings extracted/)).toBeInTheDocument()
  })

  it('unsubscribes on unmount', async () => {
    m.getActiveRun.mockResolvedValue({ run_id: 'live' })
    m.getRun.mockResolvedValue(makeRun({ run_id: 'live', status: 'running', events: [] }))
    const { unmount } = render(<App />)
    await waitFor(() => expect(subs).toHaveLength(1))
    unmount()
    expect(subs[0].off).toHaveBeenCalled()
  })
})

describe('history', () => {
  it('lists runs (nullable cost) and a click loads the run read-only', async () => {
    m.getRun.mockResolvedValue(makeRun())
    const user = await ready()
    await user.click(screen.getByRole('button', { name: 'History' }))
    expect(await screen.findByText('Mug, Dinner Plate, Candle Jar')).toBeInTheDocument()
    expect(screen.getByText(/cost —/)).toBeInTheDocument()
    await user.click(screen.getByText('Mug, Dinner Plate, Candle Jar'))
    expect(await screen.findByText('Viewing a past run (read-only)')).toBeInTheDocument()
    expect(m.getRun).toHaveBeenCalledWith('11111111-1111-4111-8111-111111111111')
    expect(screen.getByText('Status: succeeded')).toBeInTheDocument()
    expect(m.subscribe).not.toHaveBeenCalled()
  })

  it('opening a running run from history attaches to it', async () => {
    m.getRun.mockResolvedValue(makeRun({ status: 'running', events: events.slice(0, 2) }))
    const user = await ready()
    await user.click(screen.getByRole('button', { name: 'History' }))
    await user.click(await screen.findByText('Mug, Dinner Plate, Candle Jar'))
    await waitFor(() => expect(subs).toHaveLength(1))
    expect(subs[0].from).toBe(2)
  })
})

describe('history (nullable runs)', () => {
  it('opening a failed run with null fields renders without crashing', async () => {
    m.getRun.mockResolvedValue(
      makeRun({
        status: 'failed',
        error: 'Internal error',
        settings: null,
        prepared_items: null,
        prepared_boxes: null,
        final: null,
        model: null,
        packer: null,
        stats: null,
        finished_at: null,
      }),
    )
    const user = await ready()
    await user.click(screen.getByRole('button', { name: 'History' }))
    await user.click(await screen.findByText('Mug, Dinner Plate, Candle Jar'))
    expect(await screen.findByText('Viewing a past run (read-only)')).toBeInTheDocument()
    expect(screen.getByText('Status: failed')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Internal error')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
