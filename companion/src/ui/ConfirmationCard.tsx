import { useState } from 'react'
import { api } from '../api/client'
import type { ActionOccurrence } from '../api/types'
import { Panel } from './Panel'
import { useMediaQuery } from './useMediaQuery'

export function ConfirmationCard({ item, onDone, onResolved, title = 'Confirm action', confirmLabel = 'Confirm', cancelLabel = 'Cancel' }: { item: ActionOccurrence; onDone: () => Promise<unknown>; onResolved?: (item: ActionOccurrence) => void; title?: string; confirmLabel?: string; cancelLabel?: string }) {
  const [busy, setBusy] = useState<'confirm' | 'cancel' | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [resolved, setResolved] = useState<ActionOccurrence | null>(null)
  const phone = useMediaQuery('(max-width: 480px)')
  const current = resolved ?? item

  async function act(action: 'confirm' | 'cancel') {
    if (busy || current.status !== 'pending_confirmation') return
    setBusy(action)
    setMessage(action === 'confirm' ? 'Sending confirmation…' : 'Sending cancellation…')
    try {
      const result = await api<{ action: ActionOccurrence }>(`/api/actions/${item.occurrence_id}/${action}`, { method: 'POST', body: '{}' })
      setResolved(result.action)
      setMessage(result.action.status === 'uncertain' ? 'Action dispatched. Waiting for runtime verification…' : `Action ${result.action.status}.`)
      onResolved?.(result.action)
      try { await onDone() } catch { /* runtime restart may briefly interrupt refresh */ }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(null)
    }
  }

  const purgeGuarantee = current.capability_id === 'memory.purge' ? <div className="memory-purge-guarantee">
    <p>Guaranteed. After memory.purge succeeds, within atlas-work.db, in one transaction or not at all: every memory_items row in the target supersession chain is deleted with its FTS entries; every action_occurrences row for this principal under atlas/memory in a terminal status that carries a chain item id or matching normalized content has its content-bearing fields and summary replaced; evidence rows for those occurrences are scrubbed the same way. Atlas will not surface that content again through recall, chat context assembly, capability results or the control plane.</p>
    <p>Not guaranteed. The originating chat turns in atlas-chat.db (delete the conversation separately); text already sent to a model provider, subject to that provider's retention; payload_sha256, kept deliberately as attestation — a hash, not a copy; SQLite WAL frames, freelist pages and page slack (no VACUUM); any backup taken before the purge; any copy made outside Atlas.</p>
    <p className="meta">Purge is application-level suppression plus content redaction. It is not forensic erasure of the storage medium.</p>
  </div> : null

  const actionControls = current.status === 'pending_confirmation' ? <div className="actions confirmation-actions">
    <button className="confirm" type="button" disabled={Boolean(busy)} onClick={() => void act('confirm')}>{busy === 'confirm' ? 'Confirming…' : confirmLabel}</button>
    <button className="danger" type="button" disabled={Boolean(busy)} onClick={() => void act('cancel')}>{busy === 'cancel' ? 'Cancelling…' : cancelLabel}</button>
  </div> : <div className="actions confirmation-actions"><span className={`chip ${current.status === 'succeeded' ? 'done' : current.status === 'failed' || current.status === 'cancelled' ? 'failed' : 'running'}`}>{current.status}</span></div>

  const messageNode = message ? <p className={message.startsWith('Action ') ? 'meta confirmation-message' : 'offline-banner confirmation-message'}>{message}</p> : null

  return <Panel title={title} tone="decision-confirm" className="confirmation-card">
    <p className="confirmation-summary">{current.summary || current.capability_id}</p>
    {phone ? <>
      <div className="confirmation-target"><span>{current.operation}</span><strong>{current.scope}</strong></div>
      {messageNode}
      {actionControls}
      <details className="inspect confirmation-details">
        <summary>Details</summary>
        <div className="brief-row"><span>Payload hash</span><div className="mono">{current.payload_sha256}</div></div>
        {purgeGuarantee}
      </details>
    </> : <>
      <div className="brief-row"><span>Operation</span><div>{current.operation}</div></div>
      <div className="brief-row"><span>Resource</span><div>{current.scope}</div></div>
      <div className="brief-row"><span>Payload hash</span><div className="mono">{current.payload_sha256}</div></div>
      {purgeGuarantee}
      {messageNode}
      {actionControls}
    </>}
  </Panel>
}
