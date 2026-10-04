import type { PackingSettings } from '../api/types'

const isDefault = (v: unknown): boolean =>
  v == null || v === false || v === 0 || (Array.isArray(v) && v.length === 0)

const fmt = (v: unknown): string =>
  typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)

export function SettingsPanel({ settings }: { settings: PackingSettings | null }) {
  if (!settings) return null
  const { soft_instructions, ...rest } = settings
  const changed = Object.entries(rest).filter(([, v]) => !isDefault(v))
  return (
    <section aria-labelledby="settings-heading" className="space-y-1">
      <h2 id="settings-heading" className="text-base font-semibold">
        Extracted settings
      </h2>
      {changed.length === 0 ? (
        <p className="text-sm text-slate-600">defaults</p>
      ) : (
        <ul className="space-y-0.5 text-sm">
          {changed.map(([k, v]) => (
            <li key={k}>
              <span className="font-mono text-xs">{k}</span>: {fmt(v)}
            </li>
          ))}
        </ul>
      )}
      {soft_instructions.length > 0 && (
        <div>
          <h3 className="text-sm font-medium">Soft instructions</h3>
          <ul className="list-disc pl-5 text-sm">
            {soft_instructions.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
