import type { PreferenceRow } from '../api/typesV2'

type Props = {
  title: string
  rows: PreferenceRow[]
}

/** Placement preferences and how many units honoured each, for one plan. */
export function PreferencesTable({ title, rows }: Props) {
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-medium">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-600">No placement preferences.</p>
      ) : (
        <table className="text-sm">
          <caption className="sr-only">{title}: placement preferences honoured per item</caption>
          <thead className="text-xs text-slate-600">
            <tr>
              <th scope="col" className="pr-3 text-left font-medium">Item</th>
              <th scope="col" className="pr-3 text-left font-medium">Side</th>
              <th scope="col" className="pr-3 text-left font-medium">Level</th>
              <th scope="col" className="pr-3 text-left font-medium">Honoured</th>
              <th scope="col" className="text-left font-medium">Detail</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <th scope="row" className="pr-3 text-left font-normal">{r.name}</th>
                <td className="pr-3">{r.side}</td>
                <td className="pr-3">{r.level}</td>
                <td className="pr-3 tabular-nums">
                  {r.honoured === r.total ? '✓' : '✗'} {r.honoured}/{r.total}
                </td>
                <td>{r.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
