import { useMutation } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ActionOccurrence } from '../api/types'
import { Panel } from './Panel'

export function ConfirmationCard({ item, onDone }: { item: ActionOccurrence; onDone: () => Promise<unknown> }) {
  const confirm = useMutation({ mutationFn: () => api(`/api/actions/${item.occurrence_id}/confirm`, { method: 'POST', body: '{}' }), onSuccess: onDone })
  const cancel = useMutation({ mutationFn: () => api(`/api/actions/${item.occurrence_id}/cancel`, { method: 'POST', body: '{}' }), onSuccess: onDone })
  return <Panel title="Confirm exact action" tone="decision-confirm">
    <p style={{ marginTop: 0 }}>{item.summary || item.capability_id}</p>
    <div className="brief-row"><span>Operation</span><div>{item.operation}</div></div>
    <div className="brief-row"><span>Resource</span><div>{item.scope}</div></div>
    <div className="brief-row"><span>Payload hash</span><div className="mono">{item.payload_sha256}</div></div>
    <div className="actions">
      <button className="confirm" type="button" disabled={confirm.isPending} onClick={() => confirm.mutate()}>Confirm</button>
      <button className="danger" type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel</button>
    </div>
  </Panel>
}
