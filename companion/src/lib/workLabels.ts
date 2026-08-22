import type { WorkDetail, WorkListItem } from '../api/types'

export function humanWorkStatus(item: Pick<WorkListItem, 'status'> & { phase?: string }) {
  const phase = item.phase
  if (phase === 'unavailable') return { tone: 'failed', label: "Can't do this yet" }
  if (phase === 'archived') return { tone: '', label: 'Archived' }
  if (phase === 'pausing') return { tone: 'waiting', label: 'Stopping at a safe point' }
  if (phase === 'paused') return { tone: 'waiting', label: 'Paused' }
  if (phase === 'waiting_confirmation') return { tone: 'confirm', label: 'Needs confirmation' }
  if (phase === 'waiting_authority') return { tone: 'auth', label: 'Needs approval' }
  if (phase === 'running') return { tone: 'running', label: "I'm on it" }
  if (item.status === 'completed') return { tone: 'done', label: 'Done' }
  if (item.status === 'failed') return { tone: 'failed', label: "Couldn't finish" }
  if (item.status === 'cancelled') return { tone: 'waiting', label: 'Cancelled' }
  if (item.status === 'waiting') return { tone: 'waiting', label: 'Waiting' }
  if (item.status === 'active') return { tone: 'running', label: "I'm on it" }
  if (item.status === 'planned') return { tone: 'waiting', label: 'Not started' }
  return { tone: '', label: item.status }
}

export function phaseChip(detail: WorkDetail) {
  return humanWorkStatus(detail)
}

export function stepTone(status: string) {
  if (status === 'pass' || status === 'skipped') return 'done'
  if (status === 'running') return 'running'
  if (status === 'blocked') return 'waiting'
  if (status === 'failed') return 'failed'
  return ''
}

export function needsAttention(detail: WorkDetail) {
  return (
    detail.pending_confirmations.length > 0 ||
    detail.pending_approvals.length > 0 ||
    detail.phase === 'waiting_confirmation' ||
    detail.phase === 'waiting_authority' ||
    (detail.actions.includes('recover') && detail.phase !== 'terminal')
  )
}

/** True when Work can actually advance via /run (not blocked on a decision). */
export function isExecutableRun(detail: WorkDetail) {
  return (
    detail.actions.includes('run') &&
    detail.phase !== 'waiting_confirmation' &&
    detail.phase !== 'waiting_authority' &&
    detail.phase !== 'running' &&
    detail.phase !== 'pausing' &&
    detail.phase !== 'unavailable' &&
    detail.phase !== 'archived' &&
    detail.phase !== 'terminal'
  )
}

/** Honest label for the supported run/resume control. */
export function runActionLabel(detail: WorkDetail) {
  if (!isExecutableRun(detail)) return null
  if (detail.phase === 'paused') return 'Resume'
  if (detail.status === 'planned' || detail.phase === 'planned') return 'Start'
  return 'Resume'
}

const CAPABILITY_LABELS: Record<string, string> = {
  'automation.workflow': 'Understand workflow automation',
  'automation.workflow.create': 'Create an automation workflow',
  'automation.workflow.execute': 'Run an automation workflow',
  'communication.email.send': 'Send an email',
  'knowledge.index': 'Index local knowledge',
  'knowledge.search': 'Search local knowledge',
  'knowledge.answer': 'Answer from local knowledge',
  'knowledge.ingest_text': 'Index local text',
  'planning.general': 'Plan the work',
  'reasoning.general': 'Reason through the request',
  'reasoning.deep_analysis': 'Analyze the request in depth',
  'generation.compose': 'Compose the requested text',
  'coding.software_engineering': 'Implement software changes',
  'documents.multimodal': 'Interpret document pages',
}

export function humanCapabilityLabel(capabilityId: string) {
  return CAPABILITY_LABELS[capabilityId] || capabilityId.replace(/\./g, ' · ')
}
