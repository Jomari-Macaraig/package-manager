import type { Plan } from '../api/types'

export function NotesLeftovers({ plan }: { plan: Plan | null }) {
  if (!plan) return null
  return (
    <section aria-labelledby="notes-heading" className="space-y-3">
      <h2 id="notes-heading" className="text-base font-semibold">
        Notes and leftovers
      </h2>
      <div>
        <h3 className="text-sm font-medium">Notes</h3>
        {plan.notes.length ? (
          <ol className="list-decimal space-y-0.5 pl-5 text-sm">
            {plan.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-slate-600">No notes.</p>
        )}
      </div>
      <div>
        <h3 className="text-sm font-medium">Leftover items</h3>
        {plan.leftover_items.length ? (
          <ul className="space-y-1 text-sm">
            {plan.leftover_items.map((g, i) => (
              <li key={i} className="rounded border border-slate-200 p-2">
                <div>{g.items.map((it) => `${it.name} × ${it.quantity}`).join(', ')}</div>
                <div className="text-slate-600">Reason: {g.reason}</div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-600">None — everything was packed.</p>
        )}
      </div>
    </section>
  )
}
