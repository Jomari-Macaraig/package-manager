import { useState } from 'react'
import { getHistory } from '../api/client'
import type { ChatMessage, RunHistory } from '../api/types'

const text = (c: unknown): string =>
  typeof c === 'string' ? c : c == null ? '' : JSON.stringify(c, null, 2)

/** Label by position: the first system/user messages are the prompts;
 *  later user messages are validator feedback. */
function labels(messages: ChatMessage[]): string[] {
  let users = 0
  let replies = 0
  return messages.map((m) => {
    if (m.role === 'system') return 'System prompt'
    if (m.role === 'assistant') return `Model reply ${++replies}`
    return ++users === 1 ? 'User prompt' : `Feedback ${users - 1}`
  })
}

function Stage({ title, messages }: { title: string; messages: ChatMessage[] }) {
  if (messages.length === 0) return null
  const names = labels(messages)
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-medium">{title}</h3>
      {messages.map((m, i) => {
        const body = text(m.content)
        return (
          <details key={i} className="rounded border border-slate-200">
            <summary className="cursor-pointer px-2 py-1 text-sm">
              {names[i]}{' '}
              <span className="text-xs text-slate-500">
                ({m.role}, {body.length.toLocaleString()} chars)
              </span>
            </summary>
            <div className="border-t border-slate-200 p-2">
              <button
                type="button"
                className="mb-1 rounded border border-slate-300 px-1.5 text-xs hover:bg-slate-50"
                onClick={() => void navigator.clipboard?.writeText(body)}
              >
                Copy
              </button>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">
                {body}
              </pre>
            </div>
          </details>
        )
      })}
    </div>
  )
}

/** The messages sent to the model (system prompt, user prompt, feedback)
 *  and its replies. Loaded on demand; remount (key) per run and status. */
export function PromptsPanel({ runId }: { runId: string }) {
  const [history, setHistory] = useState<RunHistory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      setHistory(await getHistory(runId))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const empty =
    history && history.extraction.length === 0 && history.planning.length === 0
  return (
    <section aria-labelledby="prompts-heading" className="space-y-2">
      <h2 id="prompts-heading" className="text-base font-semibold">
        Prompts
      </h2>
      {!history && (
        <button
          type="button"
          className="rounded border border-slate-300 px-2 py-0.5 text-sm hover:bg-slate-50"
          onClick={() => void load()}
          disabled={loading}
        >
          {loading ? 'Loading…' : 'Show prompts'}
        </button>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-800">
          Could not load prompts: {error}
        </p>
      )}
      {empty && (
        <p className="text-sm text-slate-600">
          No model calls saved yet; they are saved when each stage finishes.
        </p>
      )}
      {history && (
        <>
          <Stage title="Planning" messages={history.planning} />
          <Stage title="Extraction" messages={history.extraction} />
        </>
      )}
    </section>
  )
}
