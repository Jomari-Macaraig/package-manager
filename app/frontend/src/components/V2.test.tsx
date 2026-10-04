import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { Feedback, JobChoices, JobView } from '../api/typesV2'
import { defaultJobChoices } from '../lib/editor'
import { makePlan, okSide } from '../test/fixtures'
import {
  configV2,
  evalGroups,
  jobsV2,
  makeNoPlanRunV2,
  makeRunV2,
  runV2,
  RUN_V2_ID,
} from '../test/fixturesV2'
import { jsonResponse, mockFetch, requests } from '../test/mockFetch'
import { EvaluationPage } from './EvaluationPage'
import { FeedbackControls } from './FeedbackControls'
import { HistoryList } from './HistoryList'
import { JobCard } from './JobCard'
import { JobSettings } from './JobSettings'
import { PlanPanel } from './PlanPanel'
import { RunViewV2 } from './RunViewV2'
import { VerdictsList } from './VerdictsList'

const fb = (over: Partial<Feedback> = {}): Feedback => ({
  rating: 'up',
  comment: '',
  wrong_verdicts: [],
  updated_at: '2026-01-01T00:00:00.000Z',
  ...over,
})

describe('PlanPanel with URL props', () => {
  it('uses renderSrc, downloadHref and label for a final plan', () => {
    render(
      <PlanPanel
        title="Final plan"
        planKey="final"
        side={okSide(makePlan())}
        runId="r1"
        terminal
        chosen={false}
        label="final plan"
        renderSrc={(b, u) => `/x/${b}/${u}`}
        downloadHref="/x/dl"
      />,
    )
    expect(screen.getByRole('img')).toHaveAttribute('src', '/x/1/3')
    expect(screen.getByRole('img')).toHaveAttribute('alt', 'Box 1 of the final plan, steps 1–3')
    expect(screen.getByLabelText('Box (final plan)')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Download SVG' })).toHaveAttribute('href', '/x/dl')
  })

  it('without props it still builds the v1 URLs', () => {
    render(
      <PlanPanel
        title="Model"
        planKey="packer"
        side={okSide(makePlan())}
        runId="r1"
        terminal
        chosen={false}
      />,
    )
    expect(screen.getByRole('img')).toHaveAttribute('src', '/api/runs/r1/render?plan=packer&box=1&upto=3')
    expect(screen.getByRole('link', { name: 'Download SVG' })).toHaveAttribute(
      'href',
      '/api/runs/r1/svg?plan=packer',
    )
  })
})

describe('HistoryList renderMeta', () => {
  it('replaces the model and status lines', async () => {
    const user = userEvent.setup()
    const onOpen = vi.fn()
    render(
      <HistoryList
        runs={[{ run_id: 'a', created_at: '2026-01-01T00:00:00Z', title: 'T', status: 'succeeded' }]}
        loading={false}
        error={null}
        selectedId={null}
        onOpen={onOpen}
        onRefresh={() => {}}
        renderMeta={(r) => <div>custom {r.status}</div>}
      />,
    )
    expect(screen.getByText('custom succeeded')).toBeInTheDocument()
    expect(screen.queryByText(/no plan chosen/)).not.toBeInTheDocument()
    await user.click(screen.getByText('T'))
    expect(onOpen).toHaveBeenCalledWith('a')
  })
})

describe('JobSettings', () => {
  function Harness({ onChange }: { onChange?: (v: JobChoices) => void }) {
    const [v, setV] = useState(defaultJobChoices(configV2))
    return (
      <JobSettings
        config={configV2}
        value={v}
        onChange={(n) => {
          setV(n)
          onChange?.(n)
        }}
      />
    )
  }

  it('shows each job with its default model and the budget', () => {
    render(<Harness />)
    expect(screen.getByText(/Strategy:/).closest('li')).toHaveTextContent(
      'GPT-5.4 nano (low) — default',
    )
    expect(screen.getByText(/Check:/).closest('li')).toHaveTextContent('GPT-4.1 mini — default')
    expect(screen.getByText('Budget per run: $0.02 · 30 s')).toBeInTheDocument()
  })

  it('has a labelled model select per job and an effort select only for reasoning models', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    for (const j of ['Extract', 'Strategy', 'Check', 'Notes'])
      expect(screen.getByLabelText(`${j} model`)).toBeInTheDocument()
    expect(screen.getByLabelText('Strategy effort')).toHaveValue('low')
    expect(screen.queryByLabelText('Extract effort')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Strategy model'), 'gpt-4.1-mini')
    expect(screen.queryByLabelText('Strategy effort')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Strategy model'), 'gpt-5.4-nano')
    expect(screen.getByLabelText('Strategy effort')).toHaveValue('low')
  })

  it('changing the model resets an unsupported effort; Reset restores the defaults', async () => {
    const user = userEvent.setup()
    const seen: JobChoices[] = []
    render(<Harness onChange={(v) => seen.push(v)} />)
    await user.selectOptions(screen.getByLabelText('Strategy effort'), 'high')
    expect(seen.at(-1)!.strategy).toEqual({ model: 'gpt-5.4-nano', reasoning_effort: 'high' })
    await user.selectOptions(screen.getByLabelText('Strategy model'), 'gpt-4.1-mini')
    expect(seen.at(-1)!.strategy).toEqual({ model: 'gpt-4.1-mini', reasoning_effort: null })
    expect(screen.getByText(/Strategy:/).closest('li')).toHaveTextContent('default is GPT-5.4 nano')
    await user.click(screen.getByRole('button', { name: 'Reset to defaults' }))
    expect(seen.at(-1)).toEqual(defaultJobChoices(configV2))
  })
})

describe('RunViewV2', () => {
  it('renders every section of the fixture', () => {
    render(<RunViewV2 run={runV2} />)
    for (const h of [
      'Run',
      'Final plan',
      'Jobs',
      'Plans',
      'Metrics',
      'Preferences and verdicts',
      'Extracted settings',
    ])
      expect(screen.getByRole('heading', { level: 2, name: h })).toBeInTheDocument()
    expect(screen.getByText('Status: succeeded')).toBeInTheDocument()
    expect(screen.getByText('Winner: model — honours the instructions')).toBeInTheDocument()
    expect(screen.getByText(/Cost \$0\.0027 of \$0\.0200 · 5\.0 s of 30 s/)).toBeInTheDocument()
    expect(screen.queryByText('Over budget')).not.toBeInTheDocument()
    // final plan uses the v2 final render URL, with notes as numbered steps per box
    const final = screen.getByRole('region', { name: 'Plan to pack' })
    expect(within(final).getByRole('img')).toHaveAttribute(
      'src',
      `/api/v2/runs/${RUN_V2_ID}/render?plan=final&box=1&upto=3`,
    )
    expect(screen.getByText('Load 3 × Mug')).toBeInTheDocument()
    expect(screen.getByText('Cut box 2 to 6 in.')).toBeInTheDocument()
    // plans
    expect(screen.getByRole('region', { name: 'Model' })).toHaveTextContent('Chosen')
    expect(screen.getByRole('region', { name: 'Code packer' })).not.toHaveTextContent('Chosen')
    // jobs: one card per job
    expect(screen.getAllByRole('article')).toHaveLength(4)
    // preferences on both sides, verdicts, metrics, settings
    expect(screen.getByText('3/3 on the left half')).toBeInTheDocument()
    expect(screen.getByText('1/3 on the left half')).toBeInTheDocument()
    expect(screen.getByText('Honoured', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText('Boxes used', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('Soft instructions')).toBeInTheDocument()
  })

  it('final: null says "No valid plan" and still renders the rest', () => {
    render(<RunViewV2 run={makeNoPlanRunV2()} />)
    expect(screen.getByText('No valid plan', { selector: 'p.font-medium' })).toBeInTheDocument()
    expect(screen.getByText('Winner: none — no valid plan')).toBeInTheDocument()
    expect(screen.getByText('too heavy')).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(4)
    expect(screen.getByText('No verdicts (the check was skipped).')).toBeInTheDocument()
  })

  it('shows the over-budget badge, fake flag and a run error in text', () => {
    const run = makeRunV2({
      fake: true,
      error: 'boom',
      totals: { ...runV2.totals, over_budget: true },
    })
    render(<RunViewV2 run={run} />)
    expect(screen.getByText('Over budget')).toBeInTheDocument()
    expect(screen.getByText('Fake LLM run')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Error: boom')
  })

  it('terminal run with jobs: [] shows "No job data"', () => {
    render(<RunViewV2 run={makeRunV2({ jobs: [] })} />)
    expect(screen.getByText('No job data')).toBeInTheDocument()
  })

  it('a running run shows job events as progress, no images and disabled feedback state', () => {
    const run = makeRunV2({
      status: 'running',
      finished_at: null,
      jobs: [],
      final: null,
      source: null,
      reason: null,
      totals: null,
      packer: null,
      model: null,
    })
    render(<RunViewV2 run={run} events={runV2.events.slice(0, 2)} />)
    expect(screen.getByLabelText('Job progress')).toHaveTextContent(
      'Job extract: ok (checks 2/2 passed)',
    )
    expect(screen.queryByText('No job data')).not.toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText('The final plan appears when the run finishes.')).toBeInTheDocument()
    expect(screen.queryByText('No valid plan', { selector: 'p.font-medium' })).not.toBeInTheDocument()
  })

  it('feedback calls PUT /api/v2/runs/{id}/feedback/{job} and shows Saved', async () => {
    const user = userEvent.setup()
    const f = mockFetch({
      [`PUT /api/v2/runs/${RUN_V2_ID}/feedback/strategy`]: fb({ rating: 'down', comment: 'meh' }),
    })
    render(<RunViewV2 run={runV2} />)
    const strategy = screen.getByRole('article', { name: 'Strategy' })
    await user.click(within(strategy).getByRole('button', { name: /Bad/ }))
    await user.type(within(strategy).getByLabelText('Comment on the strategy job'), 'meh')
    await user.click(within(strategy).getByRole('button', { name: 'Save feedback (strategy)' }))
    expect(await within(strategy).findByText('Saved')).toBeInTheDocument()
    const [url, init] = f.mock.calls[0]
    expect(url).toBe(`/api/v2/runs/${RUN_V2_ID}/feedback/strategy`)
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({ rating: 'down', comment: 'meh' })
  })

  it('marking a verdict wrong feeds wrong_verdicts into the check job feedback', async () => {
    const user = userEvent.setup()
    const f = mockFetch({
      [`PUT /api/v2/runs/${RUN_V2_ID}/feedback/check`]: fb({ wrong_verdicts: [1] }),
    })
    render(<RunViewV2 run={runV2} />)
    await user.click(screen.getByRole('button', { name: 'Mark wrong: kettles on the right' }))
    const check = screen.getByRole('article', { name: 'Check' })
    await user.click(within(check).getByRole('button', { name: 'Save feedback (check)' }))
    await waitFor(() => expect(f).toHaveBeenCalled())
    expect(JSON.parse(String(f.mock.calls[0][1]?.body))).toEqual({
      rating: null,
      comment: '',
      wrong_verdicts: [1],
    })
  })
})

describe('JobCard', () => {
  const card = (job: JobView, terminal = true) =>
    render(<JobCard runId="r1" job={job} terminal={terminal} onFeedback={() => {}} />)

  it('ok job: status text, model, version, checks as text, output, cost', () => {
    card(jobsV2[1])
    expect(screen.getByText('Status: ok')).toBeInTheDocument()
    expect(screen.getByText('strategy-v1')).toBeInTheDocument()
    expect(screen.getByText('gpt-5.4-nano')).toBeInTheDocument()
    expect(screen.getByText('✓ parsed')).toBeInTheDocument()
    expect(screen.getByText('✗ preferences_honoured — Jar 2/3')).toBeInTheDocument()
    expect(screen.getByText('Failed:')).toBeInTheDocument()
    expect(screen.getByText('$0.0020')).toBeInTheDocument()
    expect(screen.getByText('Output (strategy)')).toBeInTheDocument()
  })

  it('fallback job shows its error', () => {
    card(jobsV2[3])
    expect(screen.getByText('Status: fallback')).toBeInTheDocument()
    expect(screen.getByText('Error: ValidationError: bad notes')).toBeInTheDocument()
  })

  it('skipped job: no model, no feedback, no messages', () => {
    card({
      ...jobsV2[2],
      status: 'skipped',
      model: null,
      calls: 0,
      checks: [],
      output: null,
      error: 'not run',
      cost: 0,
    })
    expect(screen.getByText('Status: skipped')).toBeInTheDocument()
    expect(screen.getByText('Skipped: no feedback for this job.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Show messages/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Good/ })).not.toBeInTheDocument()
  })

  it('feedback is disabled until the run is terminal', () => {
    card(jobsV2[0], false)
    expect(screen.getByRole('button', { name: /Good/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save feedback (extract)' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /Show messages/ })).not.toBeInTheDocument()
  })

  it('loads messages lazily, once, and can hide them', async () => {
    const user = userEvent.setup()
    const f = mockFetch({
      'GET /api/v2/runs/r1/jobs/extract/messages': {
        messages: [
          { role: 'system', content: 'be brief' },
          { role: 'user', content: { a: 1 } },
        ],
      },
    })
    card(jobsV2[0])
    expect(f).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Show messages (extract)' }))
    expect(await screen.findByText('be brief')).toBeInTheDocument()
    expect(screen.getByText(/"a": 1/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Hide messages (extract)' }))
    expect(screen.queryByText('be brief')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show messages (extract)' }))
    expect(await screen.findByText('be brief')).toBeInTheDocument()
    expect(f).toHaveBeenCalledTimes(1)
  })

  it('a failed messages request is announced', async () => {
    const user = userEvent.setup()
    mockFetch({
      'GET /api/v2/runs/r1/jobs/extract/messages': jsonResponse({ detail: 'gone' }, 404),
    })
    card(jobsV2[0])
    await user.click(screen.getByRole('button', { name: 'Show messages (extract)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('gone')
  })
})

describe('FeedbackControls', () => {
  it('toggles use aria-pressed and the emoji are hidden from assistive tech', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <FeedbackControls runId="r1" job="notes" feedback={null} onSaved={() => {}} />,
    )
    const good = screen.getByRole('button', { name: 'Good' })
    const bad = screen.getByRole('button', { name: 'Bad' })
    expect(good).toHaveAttribute('aria-pressed', 'false')
    await user.click(good)
    expect(good).toHaveAttribute('aria-pressed', 'true')
    await user.click(bad)
    expect(good).toHaveAttribute('aria-pressed', 'false')
    expect(bad).toHaveAttribute('aria-pressed', 'true')
    await user.click(bad)
    expect(bad).toHaveAttribute('aria-pressed', 'false')
    expect(container.querySelectorAll('[aria-hidden="true"]')).toHaveLength(2)
  })

  it('starts from the saved feedback and sends rating null when cleared', async () => {
    const user = userEvent.setup()
    const f = mockFetch({ 'PUT /api/v2/runs/r1/feedback/notes': fb({ rating: null }) })
    const onSaved = vi.fn()
    render(
      <FeedbackControls
        runId="r1"
        job="notes"
        feedback={fb({ rating: 'up', comment: 'ok' })}
        onSaved={onSaved}
      />,
    )
    expect(screen.getByRole('button', { name: 'Good' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Comment on the notes job')).toHaveValue('ok')
    await user.click(screen.getByRole('button', { name: 'Good' }))
    await user.click(screen.getByRole('button', { name: 'Save feedback (notes)' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(JSON.parse(String(f.mock.calls[0][1]?.body))).toEqual({ rating: null, comment: 'ok' })
  })

  it('caps the comment at 2000 characters', () => {
    render(<FeedbackControls runId="r1" job="notes" feedback={null} onSaved={() => {}} />)
    expect(screen.getByLabelText('Comment on the notes job')).toHaveAttribute('maxlength', '2000')
  })

  it('shows a 422 inline and no Saved', async () => {
    const user = userEvent.setup()
    mockFetch({
      'PUT /api/v2/runs/r1/feedback/notes': jsonResponse({ detail: 'run is not finished' }, 409),
    })
    const onSaved = vi.fn()
    render(<FeedbackControls runId="r1" job="notes" feedback={null} onSaved={onSaved} />)
    await user.click(screen.getByRole('button', { name: 'Save feedback (notes)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('run is not finished')
    expect(screen.queryByText('Saved')).not.toBeInTheDocument()
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('sends wrong_verdicts only for the check job', async () => {
    const user = userEvent.setup()
    const f = mockFetch({
      'PUT /api/v2/runs/r1/feedback/check': fb(),
      'PUT /api/v2/runs/r1/feedback/strategy': fb(),
    })
    const { unmount } = render(
      <FeedbackControls runId="r1" job="check" feedback={null} wrongVerdicts={[0, 2]} onSaved={() => {}} />,
    )
    await user.click(screen.getByRole('button', { name: 'Save feedback (check)' }))
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1))
    expect(JSON.parse(String(f.mock.calls[0][1]?.body)).wrong_verdicts).toEqual([0, 2])
    unmount()
    render(
      <FeedbackControls runId="r1" job="strategy" feedback={null} wrongVerdicts={[0]} onSaved={() => {}} />,
    )
    await user.click(screen.getByRole('button', { name: 'Save feedback (strategy)' }))
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2))
    expect(JSON.parse(String(f.mock.calls[1][1]?.body))).not.toHaveProperty('wrong_verdicts')
  })
})

describe('VerdictsList', () => {
  it('each verdict has a Mark wrong toggle labelled with its instruction', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    const { rerender } = render(
      <VerdictsList verdicts={runV2.verdicts} wrong={[]} onToggle={onToggle} />,
    )
    const a = screen.getByRole('button', { name: 'Mark wrong: Keep the jars on the left side' })
    expect(a).toHaveAttribute('aria-pressed', 'false')
    await user.click(a)
    expect(onToggle).toHaveBeenCalledWith(0)
    rerender(<VerdictsList verdicts={runV2.verdicts} wrong={[0]} onToggle={onToggle} />)
    expect(
      screen.getByRole('button', { name: 'Mark wrong: Keep the jars on the left side' }),
    ).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Marked wrong')).toBeInTheDocument()
  })

  it('verdict words are text; empty list explains the skip', () => {
    const { rerender } = render(<VerdictsList verdicts={runV2.verdicts} wrong={[]} onToggle={() => {}} />)
    expect(screen.getByText('Honoured')).toBeInTheDocument()
    expect(screen.getByText('Cannot tell')).toBeInTheDocument()
    rerender(<VerdictsList verdicts={[]} wrong={[]} onToggle={() => {}} />)
    expect(screen.getByText('No verdicts (the check was skipped).')).toBeInTheDocument()
  })
})

describe('EvaluationPage', () => {
  it('renders a table per job, with — for null rates and the job-specific columns', async () => {
    const f = mockFetch({ 'GET /api/v2/eval/summary': { groups: evalGroups } })
    render(<EvaluationPage />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading evaluation')
    const strategy = await screen.findByRole('table', { name: /Strategy job/ })
    expect(within(strategy).getByRole('columnheader', { name: 'Model won % (run-level)' })).toBeInTheDocument()
    expect(within(strategy).getByRole('cell', { name: '40%' })).toBeInTheDocument()
    expect(within(strategy).getByRole('cell', { name: '80%' })).toBeInTheDocument()
    expect(within(strategy).getByText('2 checks')).toBeInTheDocument()
    expect(within(strategy).getByText('plan_valid: 90%')).toBeInTheDocument()
    const check = screen.getByRole('table', { name: /Check job/ })
    expect(within(check).getByRole('columnheader', { name: 'Verdicts marked wrong %' })).toBeInTheDocument()
    expect(within(check).getByRole('cell', { name: '25%' })).toBeInTheDocument()
    // null rates and a latency with no calls
    expect(within(check).getAllByRole('cell', { name: '—' }).length).toBeGreaterThanOrEqual(6)
    expect(screen.queryByRole('table', { name: /Notes job/ })).not.toBeInTheDocument()
    expect(requests(f)).toEqual(['GET /api/v2/eval/summary?include_fake=false'])
  })

  it('tables have a caption and row headers', async () => {
    mockFetch({ 'GET /api/v2/eval/summary': { groups: evalGroups } })
    render(<EvaluationPage />)
    const t = await screen.findByRole('table', { name: /Strategy job/ })
    expect(within(t).getByRole('rowheader', { name: 'strategy-v1' })).toBeInTheDocument()
    expect(within(t).getAllByRole('columnheader').length).toBeGreaterThan(10)
  })

  it('shows the empty state', async () => {
    mockFetch({ 'GET /api/v2/eval/summary': { groups: [] } })
    render(<EvaluationPage />)
    expect(await screen.findByText('No v2 runs yet')).toBeInTheDocument()
  })

  it('announces an error and Refresh retries', async () => {
    const user = userEvent.setup()
    let fail = true
    const f = mockFetch({
      'GET /api/v2/eval/summary': () => (fail ? jsonResponse({ detail: 'db down' }, 500) : { groups: evalGroups }),
    })
    render(<EvaluationPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('db down')
    expect(screen.queryByText('No v2 runs yet')).not.toBeInTheDocument()
    fail = false
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(await screen.findByRole('table', { name: /Strategy job/ })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(f).toHaveBeenCalledTimes(2)
  })

  it('the job filter and Include fake runs go to the query', async () => {
    const user = userEvent.setup()
    const f = mockFetch({ 'GET /api/v2/eval/summary': { groups: evalGroups } })
    render(<EvaluationPage />)
    await screen.findByRole('table', { name: /Strategy job/ })
    await user.selectOptions(screen.getByLabelText('Job'), 'check')
    await user.click(screen.getByLabelText('Include fake runs'))
    await waitFor(() =>
      expect(requests(f).at(-1)).toBe('GET /api/v2/eval/summary?job=check&include_fake=true'),
    )
    expect(screen.getByLabelText('Include fake runs')).toBeChecked()
  })
})

describe('PlanPanel step slider with renderSrc', () => {
  it('passes the chosen loading step to renderSrc', () => {
    render(
      <PlanPanel
        title="Model"
        planKey="model"
        side={okSide(makePlan())}
        runId="r1"
        terminal
        chosen={false}
        renderSrc={(b, u) => `/x/${b}/${u}`}
      />,
    )
    fireEvent.change(screen.getByLabelText('Loading step (model plan)'), { target: { value: '2' } })
    expect(screen.getByRole('img')).toHaveAttribute('src', '/x/1/2')
  })
})
