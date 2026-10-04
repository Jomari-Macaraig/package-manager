import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { PromptsPanel } from './PromptsPanel'

const ID = '11111111-1111-4111-8111-111111111111'
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

describe('PromptsPanel', () => {
  it('loads history on demand and labels prompts, replies and feedback', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      json({
        extraction: [],
        planning: [
          { role: 'system', content: 'You are a 3D packing planner.' },
          { role: 'user', content: 'items (1 rows)\nStarting plan ...' },
          { role: 'assistant', content: '{"boxes":[]}' },
          { role: 'user', content: 'validate_plan: INVALID' },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    render(<PromptsPanel runId={ID} />)
    expect(fetchMock).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Show prompts' }))
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/runs/${ID}/history`)
    expect(await screen.findByText('System prompt')).toBeInTheDocument()
    expect(screen.getByText('User prompt')).toBeInTheDocument()
    expect(screen.getByText('Model reply 1')).toBeInTheDocument()
    expect(screen.getByText('Feedback 1')).toBeInTheDocument()
    expect(screen.getByText('You are a 3D packing planner.')).toBeInTheDocument()
    expect(screen.queryByText('Extraction')).not.toBeInTheDocument()
  })

  it('says when nothing is saved yet', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(json({ extraction: [], planning: [] }))
    )
    render(<PromptsPanel runId={ID} />)
    await userEvent.click(screen.getByRole('button', { name: 'Show prompts' }))
    expect(await screen.findByText(/No model calls saved yet/)).toBeInTheDocument()
  })

  it('shows an error when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'run not found' }, 404)))
    render(<PromptsPanel runId={ID} />)
    await userEvent.click(screen.getByRole('button', { name: 'Show prompts' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('run not found')
  })
})
