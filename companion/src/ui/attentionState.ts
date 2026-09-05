import type { AttentionItem } from '../api/types'
import type { LampTone } from './operationState'

export function attentionTone(item: AttentionItem): LampTone {
  if (item.kind === 'handoff_unconfirmed' || item.kind === 'lapsed_obligation') return 'red'
  return 'amber'
}

export function attentionStatus(item: AttentionItem) {
  if (item.kind === 'unserviced_obligation') return 'unserviced'
  if (item.kind === 'servicing_blocked') return item.status || 'blocked'
  if (item.kind === 'handoff_unconfirmed') return 'handoff unconfirmed'
  if (item.kind === 'lapsed_obligation') return 'lapsed'
  return 'capabilities changed'
}

export function attentionTitle(item: AttentionItem) {
  if (item.kind === 'stale_unserviceable') return 'A previously unserviceable request may have a new route'
  return item.text
}

export function attentionDetail(item: AttentionItem) {
  if (item.kind === 'unserviced_obligation') return 'Atlas still owes this outcome and no active mechanism is servicing it.'
  if (item.kind === 'servicing_blocked') return `Servicing is ${item.status || 'blocked'}${item.work_id ? ` · ${item.work_id}` : ''}`
  if (item.kind === 'handoff_unconfirmed') return 'The owner response handoff could not be proven, so execution remains held.'
  if (item.kind === 'lapsed_obligation') return `The requested time bound has passed${item.lapsed_at ? ` · ${item.lapsed_at}` : ''}`
  return item.text
}

export function attentionHref(item: AttentionItem) {
  if (item.work_id) return `/work/${encodeURIComponent(item.work_id)}`
  if (item.conversation_id) return `/chat?conversation=${encodeURIComponent(item.conversation_id)}`
  return '/operations'
}
