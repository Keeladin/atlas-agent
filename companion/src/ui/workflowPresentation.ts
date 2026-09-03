import type { ChatFocus } from '../api/types'

export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export type WorkflowStep = { capability_id: string; description?: string; input?: Record<string, unknown> }

export function scheduleLabel(schedule: Record<string, unknown>) {
  const kind = String(schedule.kind ?? '')
  if (kind === 'interval') return `Every ${Number(schedule.minutes ?? 0)} minutes`
  const time = `${String(schedule.hour ?? 8).padStart(2, '0')}:${String(schedule.minute ?? 0).padStart(2, '0')}`
  if (kind === 'weekly') return `${WEEKDAYS[Number(schedule.weekday ?? 0)] ?? 'Weekly'} at ${time}`
  return `Daily at ${time}`
}

export function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

export function readSteps(value: unknown): WorkflowStep[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(row => {
    if (!row || typeof row !== 'object') return []
    const step = row as Record<string, unknown>
    const capability = typeof step.capability_id === 'string' ? step.capability_id : ''
    if (!capability) return []
    return [{
      capability_id: capability,
      description: typeof step.description === 'string' ? step.description : undefined,
      input: step.input && typeof step.input === 'object' ? step.input as Record<string, unknown> : {},
    }]
  })
}

export function stepLabel(step: WorkflowStep) {
  return step.description?.trim() || step.capability_id
}

export function focusQuery(focus: ChatFocus) {
  const params = new URLSearchParams()
  if (focus.cadence_id) params.set('cadence_id', focus.cadence_id)
  if (focus.work_id) params.set('work_id', focus.work_id)
  if (focus.step_ordinal !== undefined) params.set('step_ordinal', String(focus.step_ordinal))
  return params.toString()
}

export function readFocus(params: URLSearchParams): ChatFocus | null {
  const focus: ChatFocus = {}
  const cadence = params.get('cadence_id')
  const work = params.get('work_id')
  const ordinal = params.get('step_ordinal')
  if (cadence) focus.cadence_id = cadence
  if (work) focus.work_id = work
  if (ordinal && Number.isFinite(Number(ordinal))) focus.step_ordinal = Number(ordinal)
  return Object.keys(focus).length ? focus : null
}

export type WorkflowVariant = 'cadence-created' | 'cadence-updated' | 'cadence-run' | 'work-created'

const VARIANT_BY_CAPABILITY: Record<string, WorkflowVariant> = {
  'cadence.create': 'cadence-created',
  'cadence.update': 'cadence-updated',
  'cadence.run_now': 'cadence-run',
  'work.create': 'work-created',
}

export function workflowVariant(capabilityId?: string | null): WorkflowVariant | null {
  return capabilityId ? VARIANT_BY_CAPABILITY[capabilityId] ?? null : null
}
