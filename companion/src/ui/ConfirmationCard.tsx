import { useState } from 'react'
import { api } from '../api/client'
import type { ActionOccurrence } from '../api/types'
import { Panel } from './Panel'

export function ConfirmationCard({ item, onDone }: { item: ActionOccurrence; onDone: () => Promise<unknown> }) {
  const [busy, setBusy] = useState<'confirm' | 'cancel' | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  async function act(action: 'confirm' | 'cancel') {
    if (busy) return
    setBusy(action)
    setMessage(action === 'confirm' ? 'Sending confirmation…' : 'Sending cancellation…')
    try {
      const result = await api<{ action: ActionOccurrence }>(`/api/actions/${item.occurrence_id}/${action}`, { method: 'POST', body: '{}' })
      setMessage(`Action ${result.action.status}.`)
      await onDone()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(null)
    }
  }

  return <Panel title="Confirm exact action" tone="decision-confirm">
    <p style={{ marginTop: 0 }}>{item.summary || item.capability_id}</p>
    <div className="brief-row"><span>Operation</span><div>{item.operation}</div></div>
    <div className="brief-row"><span>Resource</span><div>{item.scope}</div></div>
    <div className="brief-row"><span>Payload hash</span><div className="mono">{item.payload_sha256}</div></div>
    {item.capability_id === 'memory.purge' ? <div className="memory-purge-guarantee">
      <p>Guaranteed. After memory.purge succeeds, within atlas-work.db, in one transaction or not at all: every memory_items row in the target supersession chain is deleted with its FTS entries; every action_occurrences row for this principal under atlas/memory in a terminal status that carries a chain item id or matching normalized content has its content-bearing fields and summary replaced; evidence rows for those occurrences are scrubbed the same way. Atlas will not surface that content again through recall, chat context assembly, capability results or the control plane.</p>
      <p>Not guaranteed. The originating chat turns in atlas-chat.db (delete the conversation separately); text already sent to a model provider, subject to that provider's retention; payload_sha256, kept deliberately as attestation — a hash, not a copy; SQLite WAL frames, freelist pages and page slack (no VACUUM); any backup taken before the purge; any copy made outside Atlas.</p>
      <p className="meta">Purge is application-level suppression plus content redaction. It is not forensic erasure of the storage medium.</p>
    </div> : null}
    {message ? <p className={message.startsWith('Action ') ? 'meta' : 'offline-banner'}>{message}</p> : null}
    <div className="actions">
      <button className="confirm" type="button" disabled={Boolean(busy)} onClick={() => void act('confirm')}>{busy === 'confirm' ? 'Confirming…' : 'Confirm'}</button>
      <button className="danger" type="button" disabled={Boolean(busy)} onClick={() => void act('cancel')}>{busy === 'cancel' ? 'Cancelling…' : 'Cancel'}</button>
    </div>
  </Panel>
}
