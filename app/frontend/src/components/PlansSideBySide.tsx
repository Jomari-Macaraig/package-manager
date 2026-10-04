import { isTerminal } from '../api/client'
import type { Run } from '../api/types'
import { PlanPanel } from './PlanPanel'

export function PlansSideBySide({ run }: { run: Run }) {
  const terminal = isTerminal(run.status)
  const chosen = run.final?.source ?? null
  return (
    <section aria-labelledby="plans-heading" className="space-y-2">
      <h2 id="plans-heading" className="text-base font-semibold">
        Plans
      </h2>
      <div className="grid gap-3 lg:grid-cols-2">
        {/* key by run so box/step selection resets for a different run */}
        <PlanPanel
          key={`${run.run_id}-model`}
          title="Model"
          planKey="model"
          side={run.model}
          runId={run.run_id}
          terminal={terminal}
          chosen={chosen === 'model'}
        />
        <PlanPanel
          key={`${run.run_id}-packer`}
          title="Code packer"
          planKey="packer"
          side={run.packer}
          runId={run.run_id}
          terminal={terminal}
          chosen={chosen === 'packer'}
        />
      </div>
      {terminal && run.status === 'succeeded' && !run.final && (
        <p className="text-sm font-medium text-red-800">Neither side produced a valid plan.</p>
      )}
    </section>
  )
}
