import type { RunEvent } from '../api/types'
import type { RunEventV2 } from '../api/typesV2'

export function formatEvent(e: RunEvent): string {
  switch (e.type) {
    case 'started':
      return 'Run started'
    case 'extracted':
      return e.skipped
        ? 'Settings extraction skipped (no instructions)'
        : `Settings extracted (${e.tokens} tokens)`
    case 'prepared':
      return `Prepared ${e.item_types} item types, ${e.units} units`
    case 'packer':
      return e.ok
        ? `Code packer: ${e.boxes} ${e.boxes === 1 ? 'box' : 'boxes'}`
        : `Code packer: invalid plan, ${e.errors_count} errors`
    case 'round':
      return `Round ${e.n} (${e.kind}): ${e.valid ? 'valid' : `invalid, ${e.errors_count} errors`}`
    case 'chosen':
      return e.source === 'model'
        ? 'Chosen plan: model'
        : e.source === 'packer'
          ? 'Chosen plan: code packer'
          : 'No valid plan from either side'
    case 'end':
      return `Run ${e.status}${e.error ? `: ${e.error}` : ''}`
  }
}

export function formatEventV2(e: RunEventV2): string {
  switch (e.type) {
    case 'started':
      return 'Run started'
    case 'job':
      return e.status === 'skipped'
        ? `Job ${e.job}: skipped`
        : `Job ${e.job}: ${e.status} (checks ${e.passed}/${e.total} passed)`
    case 'packer':
      return e.ok
        ? `Code packer: ${e.boxes} ${e.boxes === 1 ? 'box' : 'boxes'}`
        : 'Code packer: no valid plan'
    case 'chosen':
      return e.source === 'model'
        ? `Chosen plan: model (${e.reason})`
        : e.source === 'packer'
          ? `Chosen plan: code packer (${e.reason})`
          : `No valid plan (${e.reason})`
    case 'end':
      return `Run ${e.status}${e.error ? `: ${e.error}` : ''}`
  }
}
