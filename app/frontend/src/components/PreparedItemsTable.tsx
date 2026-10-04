import type { PreparedItem } from '../api/types'

function flags(i: PreparedItem): string {
  const f: string[] = []
  if (i.fixed) f.push('fixed orientation')
  if (i.upright) f.push('upright')
  if (i.no_stack) f.push('no stacking on top')
  if (i.max_load != null) f.push(`max load on top ${i.max_load} lb`)
  if (i.floor_only) f.push('floor only')
  return f.length ? f.join(', ') : '—'
}

export function PreparedItemsTable({ items }: { items: PreparedItem[] | null }) {
  if (!items) return null
  return (
    <section aria-labelledby="prepared-heading" className="space-y-1">
      <h2 id="prepared-heading" className="text-base font-semibold">
        Prepared items
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Items after settings were applied</caption>
          <thead className="text-xs text-slate-600">
            <tr>
              <th scope="col">Id</th>
              <th scope="col">Name</th>
              <th scope="col">X (in)</th>
              <th scope="col">Y (in)</th>
              <th scope="col">Z (in)</th>
              <th scope="col">Weight (lb)</th>
              <th scope="col">Qty</th>
              <th scope="col">Flags</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id} className="border-t border-slate-100">
                <td>{i.id}</td>
                <td>{i.name}</td>
                <td>{i.dimensions.x}</td>
                <td>{i.dimensions.y}</td>
                <td>{i.dimensions.z}</td>
                <td>{i.weight}</td>
                <td>{i.quantity}</td>
                <td>{flags(i)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
