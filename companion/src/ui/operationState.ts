export type LampTone = 'green' | 'amber' | 'red' | 'dim'

const GREEN_STATES = new Set(['approved', 'completed', 'enabled', 'established', 'healthy', 'managed', 'succeeded', 'verified'])
const AMBER_STATES = new Set(['active', 'executing', 'in_progress', 'paused', 'review_required', 'reviewed', 'running', 'waiting'])
const RED_STATES = new Set(['blocked', 'cancelled', 'failed', 'pending_confirmation', 'rejected', 'uncertain'])

function normalized(value?: string | null) {
  return String(value ?? '').trim().toLowerCase().replaceAll(' ', '_')
}

export function runtimeStateToLamp(value?: string | null): LampTone {
  const state = normalized(value)
  if (GREEN_STATES.has(state)) return 'green'
  if (AMBER_STATES.has(state)) return 'amber'
  if (RED_STATES.has(state)) return 'red'
  return 'dim'
}

export function reviewStateToLamp(value?: string | null): LampTone {
  const state = normalized(value)
  if (state === 'approved') return 'green'
  if (state === 'reviewed') return 'amber'
  if (state === 'rejected') return 'red'
  return 'dim'
}

export function workStateToLamp(value?: string | null): LampTone {
  const state = normalized(value)
  if (state === 'completed') return 'green'
  if (['active', 'running', 'waiting', 'paused'].includes(state)) return 'amber'
  if (['failed', 'blocked', 'pending_confirmation', 'cancelled', 'uncertain'].includes(state)) return 'red'
  return 'dim'
}

export function cadenceStateToLamp(enabled: boolean, nextRunAt?: string | null): LampTone {
  if (!enabled) return 'dim'
  if (nextRunAt) {
    const next = new Date(nextRunAt).getTime()
    if (Number.isFinite(next) && next < Date.now()) return 'amber'
  }
  return 'green'
}
