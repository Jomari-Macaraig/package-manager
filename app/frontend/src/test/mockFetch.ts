import { vi } from 'vitest'

type Req = { url: string; method: string; body: unknown }
export type Responder = unknown | Response | ((req: Req) => unknown | Response)

export const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

/**
 * Stub global fetch with routes keyed `METHOD /path` (with or without the query
 * string). Unmatched requests answer 404 {detail: "no route"}. Returns the mock
 * so tests can inspect `mock.calls`.
 */
export function mockFetch(routes: Record<string, Responder>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const key = `${method} ${url}`
    const bare = `${method} ${url.split('?')[0]}`
    const r = key in routes ? routes[key] : routes[bare]
    if (r === undefined) return jsonResponse({ detail: 'no route' }, 404)
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    const out = typeof r === 'function' ? (r as (q: Req) => unknown)({ url, method, body }) : r
    return out instanceof Response ? out : jsonResponse(out)
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

/** The `[method, url]` pairs requested so far. */
export const requests = (fn: ReturnType<typeof mockFetch>) =>
  fn.mock.calls.map(([u, i]) => `${(i?.method ?? 'GET').toUpperCase()} ${String(u)}`)
