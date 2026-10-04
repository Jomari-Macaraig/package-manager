import type { Config } from '../api/types'
import type { ModelChoice } from '../lib/editor'

type Props = {
  config: Config
  value: ModelChoice
  onChange: (v: ModelChoice) => void
  disabled?: boolean
}

const select = 'rounded border border-slate-300 px-2 py-1 text-sm'

export function ModelSettings({ config, value, onChange, disabled }: Props) {
  const planning = config.planning_models.find((m) => m.id === value.planningModel)
  const efforts = planning?.efforts ?? []

  const setPlanning = (id: string) => {
    const m = config.planning_models.find((x) => x.id === id)
    const list = m?.efforts ?? []
    const effort = list.includes(value.effort) ? value.effort : (list[0] ?? value.effort)
    onChange({ ...value, planningModel: id, effort })
  }

  return (
    <section aria-labelledby="models-heading" className="space-y-2">
      <h2 id="models-heading" className="text-base font-semibold">
        Models
      </h2>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <label className="text-sm">
          Extraction model{' '}
          <select
            className={select}
            value={value.extractionModel}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, extractionModel: e.target.value })}
          >
            {config.extraction_models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          Planning model{' '}
          <select
            className={select}
            value={value.planningModel}
            disabled={disabled}
            onChange={(e) => setPlanning(e.target.value)}
          >
            {config.planning_models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        {efforts.length > 0 && (
          <label className="text-sm">
            Reasoning effort{' '}
            <select
              className={select}
              value={value.effort}
              disabled={disabled}
              onChange={(e) =>
                onChange({ ...value, effort: e.target.value as ModelChoice['effort'] })
              }
            >
              {efforts.map((e) => (
                <option key={e} value={e}>
                  {e}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <p className="text-xs text-slate-500">
        max rounds {config.loop.max_rounds} · improve rounds {config.loop.improve_rounds}
      </p>
    </section>
  )
}
