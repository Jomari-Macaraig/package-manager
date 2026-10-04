import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { EvalGroup, Feedback, JobView } from '../api/typesV2'
import { evalGroups, jobsV2, makeNoPlanRunV2, makeRunV2 } from '../test/fixturesV2'
import { jsonResponse, mockFetch } from '../test/mockFetch'
import { EvaluationPage } from './EvaluationPage'
import { FeedbackControls } from './FeedbackControls'
import { JobCard } from './JobCard'
import { RunViewV2 } from './RunViewV2'

const fb = (over: Partial<Feedback> = {}): Feedback => ({
  rating: 'up',
  comment: '',
  wrong_verdicts: [],
  updated_at: '2026-01-01T00:00:00.000Z',
  ...over,
})

const card = (job: JobView, terminal = true) =>
  render(<JobCard runId="r1" job={job} terminal={terminal} onFeedback={() => {}} />)

describe('JobCard states and checks', () => {
  it('all checks passing show only passed marks', () => {
    card(jobsV2[0])
    const list = screen.getByRole('list', { name: 'Extract code checks' })
    expect(within(list).queryByText('Failed:')).not.toBeInTheDocument()
    expect(within(list).getAllByText('Passed:').length).toBe(jobsV2[0].checks.length)
    expect(screen.queryByText(/^Error:/)).not.toBeInTheDocument()
  })

  it('a fallback job lists each failing check with its detail and the error', () => {
    card({
      ...jobsV2[3],
      status: 'fallback',
      error: 'checks failed: step_count, counts_match',
      checks: [
        { name: 'parsed', passed: true, detail: '' },
        { name: 'step_count', passed: false, detail: 'box 1: 0 steps' },
        { name: 'counts_match', passed: false, detail: 'box 1: 7 × Jar, expected 4' },
      ],
    })
    expect(screen.getByText('Status: fallback')).toBeInTheDocument()
    expect(screen.getByText('✗ step_count — box 1: 0 steps')).toBeInTheDocument()
    expect(screen.getByText('✗ counts_match — box 1: 7 × Jar, expected 4')).toBeInTheDocument()
    expect(screen.getByText('✓ parsed')).toBeInTheDocument()
    expect(screen.getAllByText('Failed:')).toHaveLength(2)
    expect(screen.getByText('Error: checks failed: step_count, counts_match')).toBeInTheDocument()
  })

  it('a skipped job with no checks renders no check list but keeps the error text', () => {
    card({ ...jobsV2[2], status: 'skipped', model: null, calls: 0, checks: [], error: 'cancelled' })
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
    expect(screen.getByText('Error: cancelled')).toBeInTheDocument()
    expect(screen.queryByText(/^Output/)).not.toBeInTheDocument()
  })

  it('shows the saved feedback in its controls', () => {
    card({ ...jobsV2[0], feedback: fb({ rating: 'down', comment: 'wrong' }) })
    expect(screen.getByRole('button', { name: 'Bad' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Comment on the extract job')).toHaveValue('wrong')
  })

  it('an empty messages list says so', async () => {
    const user = userEvent.setup()
    mockFetch({ 'GET /api/v2/runs/r1/jobs/extract/messages': { messages: [] } })
    card(jobsV2[0])
    await user.click(screen.getByRole('button', { name: 'Show messages (extract)' }))
    expect(await screen.findByText('No messages recorded.')).toBeInTheDocument()
  })
})

describe('FeedbackControls requests and errors', () => {
  it('sends a comment-only save with rating null and shows Saved, clearing it on edit', async () => {
    const user = userEvent.setup()
    const f = mockFetch({ 'PUT /api/v2/runs/r1/feedback/strategy': fb({ rating: null, comment: 'x' }) })
    const onSaved = vi.fn()
    render(<FeedbackControls runId="r1" job="strategy" feedback={null} onSaved={onSaved} />)
    await user.type(screen.getByLabelText('Comment on the strategy job'), 'x')
    await user.click(screen.getByRole('button', { name: 'Save feedback (strategy)' }))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    expect(JSON.parse(String(f.mock.calls[0][1]?.body))).toEqual({ rating: null, comment: 'x' })
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ comment: 'x' }))
    await user.type(screen.getByLabelText('Comment on the strategy job'), 'y')
    expect(screen.queryByText('Saved')).not.toBeInTheDocument()
  })

  it('shows a real 422 validation message inline', async () => {
    const user = userEvent.setup()
    mockFetch({
      'PUT /api/v2/runs/r1/feedback/check': jsonResponse({ detail: 'wrong_verdicts index 3 out of range' }, 422),
    })
    const onSaved = vi.fn()
    render(<FeedbackControls runId="r1" job="check" feedback={null} wrongVerdicts={[3]} onSaved={onSaved} />)
    await user.click(screen.getByRole('button', { name: 'Save feedback (check)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('wrong_verdicts index 3 out of range')
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('a network failure is announced and a retry clears the error', async () => {
    const user = userEvent.setup()
    let fail = true
    mockFetch({
      'PUT /api/v2/runs/r1/feedback/notes': () => {
        if (fail) throw new Error('network down')
        return fb()
      },
    })
    render(<FeedbackControls runId="r1" job="notes" feedback={null} onSaved={() => {}} />)
    await user.click(screen.getByRole('button', { name: 'Save feedback (notes)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Network error')
    fail = false
    await user.click(screen.getByRole('button', { name: 'Save feedback (notes)' }))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('disabled controls cannot be used', () => {
    render(<FeedbackControls runId="r1" job="notes" feedback={null} disabled onSaved={() => {}} />)
    expect(screen.getByRole('button', { name: 'Good' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Bad' })).toBeDisabled()
    expect(screen.getByLabelText('Comment on the notes job')).toBeDisabled()
  })
})

describe('EvaluationPage groups', () => {
  const group = (over: Partial<EvalGroup>): EvalGroup => ({ ...evalGroups[0], ...over })

  it('one table per job with a row per version, model and effort; notes has no extra column', async () => {
    mockFetch({
      'GET /api/v2/eval/summary': {
        groups: [
          group({ job: 'strategy', prompt_version: 'strategy-v1' }),
          group({ job: 'strategy', prompt_version: 'strategy-v2', model: 'gpt-4.1', reasoning_effort: null }),
          group({ job: 'notes', prompt_version: 'notes-v1', model_won_rate: null, wrong_verdict_rate: null }),
        ],
      },
    })
    render(<EvaluationPage />)
    const strategy = await screen.findByRole('table', { name: /Strategy job/ })
    expect(within(strategy).getByRole('rowheader', { name: 'strategy-v1' })).toBeInTheDocument()
    expect(within(strategy).getByRole('rowheader', { name: 'strategy-v2' })).toBeInTheDocument()
    expect(within(strategy).getAllByRole('row')).toHaveLength(3)
    const notes = screen.getByRole('table', { name: /Notes job/ })
    expect(within(notes).queryByRole('columnheader', { name: /Model won/ })).not.toBeInTheDocument()
    expect(within(notes).queryByRole('columnheader', { name: /marked wrong/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('table', { name: /Extract job/ })).not.toBeInTheDocument()
  })

  it('a group with one check says "1 check" and every null rate shows a dash, never 0%', async () => {
    mockFetch({
      'GET /api/v2/eval/summary': {
        groups: [
          group({
            job: 'extract',
            calls: 0,
            skipped: 3,
            ok_rate: null,
            fallback_rate: null,
            all_checks_rate: null,
            avg_cost: null,
            p50_latency_s: null,
            p95_latency_s: null,
            per_check: { parsed: 0.5 },
          }),
        ],
      },
    })
    render(<EvaluationPage />)
    const t = await screen.findByRole('table', { name: /Extract job/ })
    expect(within(t).getByText('1 check')).toBeInTheDocument()
    expect(within(t).getByText('parsed: 50%')).toBeInTheDocument()
    expect(within(t).getAllByRole('cell', { name: '—' }).length).toBeGreaterThanOrEqual(6)
    expect(within(t).queryByText('0%')).not.toBeInTheDocument()
  })

  it('shows loading, then the empty state, with no table', async () => {
    mockFetch({ 'GET /api/v2/eval/summary': { groups: [] } })
    render(<EvaluationPage />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading evaluation')
    expect(await screen.findByText('No v2 runs yet')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

describe('RunViewV2 edge combinations', () => {
  it('final null together with jobs [] shows both notices', () => {
    render(<RunViewV2 run={{ ...makeNoPlanRunV2(), jobs: [] }} />)
    expect(screen.getByText('No valid plan', { selector: 'p.font-medium' })).toBeInTheDocument()
    expect(screen.getByText('No job data')).toBeInTheDocument()
    expect(screen.queryAllByRole('article')).toHaveLength(0)
  })

  it('a failed run (no final, no jobs) shows its error and no feedback controls', () => {
    render(
      <RunViewV2
        run={makeRunV2({ status: 'failed', error: 'bad key', final: null, jobs: [], source: null, reason: null })}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Error: bad key')
    expect(screen.queryByRole('button', { name: /Save feedback/ })).not.toBeInTheDocument()
  })

  it('a skipped job card in a terminal run offers no feedback', () => {
    const jobs = makeRunV2().jobs.map((j) =>
      j.job === 'check' ? { ...j, status: 'skipped' as const, checks: [], model: null, calls: 0 } : j,
    )
    render(<RunViewV2 run={makeRunV2({ jobs })} />)
    const check = screen.getByRole('article', { name: 'Check' })
    expect(within(check).getByText('Skipped: no feedback for this job.')).toBeInTheDocument()
    expect(within(check).queryByRole('button', { name: /Save feedback/ })).not.toBeInTheDocument()
  })
})
