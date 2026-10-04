import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { Run } from '../api/types'
import { makePlan, makeRun, okSide } from '../test/fixtures'
import { PlanPanel } from './PlanPanel'
import { RunView } from './RunView'

const ID = '11111111-1111-4111-8111-111111111111'
const base = `/api/runs/${ID}/render`

describe('PlanPanel', () => {
  const panel = (over: Partial<Parameters<typeof PlanPanel>[0]> = {}) =>
    render(
      <PlanPanel
        title="Model"
        planKey="model"
        side={okSide(makePlan())}
        runId={ID}
        terminal
        chosen={false}
        {...over}
      />,
    )

  it('invalid side shows errors and no image', () => {
    panel({
      side: {
        ok: false,
        rank: null,
        errors: ['box 1 overweight', 'model call failed: timeout'],
        hints: [],
        plan: null,
        box_count: 0,
        rounds: 3,
        improved: false,
      },
    })
    expect(screen.getByText('Invalid plan')).toBeInTheDocument()
    expect(screen.getByText('box 1 overweight')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('box picker and slider produce the right render URL; slider defaults to max', () => {
    panel()
    const img = screen.getByRole('img')
    expect(img).toHaveAttribute('src', `${base}?plan=model&box=1&upto=3`)
    expect(img).toHaveAttribute('alt', 'Box 1 of the model plan, steps 1–3')
    fireEvent.change(screen.getByLabelText('Loading step (model plan)'), { target: { value: '2' } })
    expect(screen.getByRole('img')).toHaveAttribute('src', `${base}?plan=model&box=1&upto=2`)
  })

  it('clamps the step when switching to a box with fewer items, and labels options', async () => {
    const user = userEvent.setup()
    panel()
    expect(screen.getByRole('option', { name: 'Box 2 — L-14' })).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Box (model plan)'), '2')
    expect(screen.getByRole('img')).toHaveAttribute('src', `${base}?plan=model&box=2&upto=2`)
    const slider = screen.getByLabelText('Loading step (model plan)')
    expect(slider).toHaveAttribute('max', '2')
    expect(slider).toHaveValue('2')
    // go back to box 1 with step 2 chosen explicitly -> stays 2, clamped only when needed
    fireEvent.change(slider, { target: { value: '1' } })
    await user.selectOptions(screen.getByLabelText('Box (model plan)'), '1')
    expect(screen.getByRole('img')).toHaveAttribute('src', `${base}?plan=model&box=1&upto=1`)
  })

  it('uses the code packer wording and download link', () => {
    panel({ title: 'Code packer', planKey: 'packer' })
    expect(screen.getByRole('img')).toHaveAttribute(
      'alt',
      'Box 1 of the code packer plan, steps 1–3',
    )
    expect(screen.getByRole('link', { name: 'Download SVG' })).toHaveAttribute(
      'href',
      `/api/runs/${ID}/svg?plan=packer`,
    )
  })

  it('shows "Chosen" text when chosen', () => {
    panel({ chosen: true })
    expect(screen.getByText('Chosen')).toBeInTheDocument()
  })

  it('shows rank with labels', () => {
    panel()
    expect(screen.getByText('Leftover units')).toBeInTheDocument()
    expect(screen.getByText('Shipped volume (in³)')).toBeInTheDocument()
  })

  it('0 boxes: "No boxes used", no image', () => {
    const plan = { ...makePlan(), boxes: [] }
    panel({ side: okSide(plan, [5, 0, 0, 0]) })
    expect(screen.getByText('No boxes used')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('no image while the run is not terminal', () => {
    panel({ terminal: false })
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('falls back to text when the image fails to load', () => {
    panel()
    fireEvent.error(screen.getByRole('img'))
    expect(screen.getByText('Image could not be loaded.')).toBeInTheDocument()
  })
})

describe('RunView', () => {
  it('marks the winner with "Chosen" and conveys status as text', () => {
    render(<RunView run={makeRun()} />)
    expect(screen.getAllByText('Chosen')).toHaveLength(1)
    const chosen = screen.getByRole('region', { name: 'Code packer' })
    expect(chosen).toHaveTextContent('Chosen')
    expect(screen.getByRole('region', { name: 'Model' })).not.toHaveTextContent('Chosen')
    expect(screen.getByText('Status: succeeded')).toBeInTheDocument()
  })

  it('renders stats, settings, prepared items, notes and leftovers', () => {
    render(<RunView run={makeRun()} />)
    expect(screen.getByText(/Total: 3200 tokens/)).toBeInTheDocument()
    expect(screen.getByText('padding', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('Cut box 2 to 6 in.')).toBeInTheDocument()
    expect(screen.getByText(/Reason: Heavier than any box allows/)).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'upright' })).toBeInTheDocument()
  })

  it('renders LLM text as plain text, never HTML', () => {
    const { container } = render(<RunView run={makeRun()} />)
    expect(screen.getByText('Ship it fast <i>please</i>')).toBeInTheDocument()
    expect(container.querySelector('b, i')).toBeNull()
  })

  it('failed run with null final/model/packer/settings/stats renders without crashing', () => {
    const run: Run = makeRun({
      status: 'failed',
      error: 'Invalid API key',
      settings: null,
      prepared_items: null,
      prepared_boxes: null,
      final: null,
      model: null,
      packer: null,
      stats: null,
      finished_at: null,
    })
    render(<RunView run={run} />)
    expect(screen.getByText('Status: failed')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Error: Invalid API key')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getAllByText('No result for this side.')).toHaveLength(2)
  })

  it('cancelled run with partial stats (null extraction) renders', () => {
    const run = makeRun({ status: 'cancelled', final: null })
    run.stats!.extraction = null
    render(<RunView run={run} />)
    expect(screen.getByText('not called')).toBeInTheDocument()
    expect(screen.queryByText('Chosen')).not.toBeInTheDocument()
  })

  it('a running run offers no images', () => {
    render(<RunView run={makeRun({ status: 'running', finished_at: null })} />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('shows "defaults" when no settings differ', () => {
    const run = makeRun()
    run.settings = { ...run.settings!, padding: 0, upright: [], soft_instructions: [] }
    render(<RunView run={run} />)
    expect(screen.getByText('defaults')).toBeInTheDocument()
  })
})

describe('settings and notes', () => {
  it('lists soft instructions separately from changed settings; notes are an ordered list', () => {
    const run = makeRun()
    run.settings = { ...run.settings!, padding: 0.5, soft_instructions: ['Ship it fast'] }
    run.final!.plan.notes = ['first note', 'second note']
    render(<RunView run={run} />)
    const soft = screen.getByRole('heading', { name: 'Soft instructions' })
    expect(soft.parentElement).toHaveTextContent('Ship it fast')
    const padding = screen.getByText('padding').closest('li')
    expect(padding).toHaveTextContent('padding: 0.5')
    expect(padding).not.toHaveTextContent('Ship it fast')
    const notes = screen
      .getAllByRole('listitem')
      .filter((li) => li.parentElement?.tagName === 'OL')
    expect(notes.map((li) => li.textContent)).toEqual(['first note', 'second note'])
  })
})
