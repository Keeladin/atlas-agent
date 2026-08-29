import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionOccurrence, WorkItem } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { Panel } from '../ui/Panel'
import { Workspace } from '../ui/Workspace'

export function WorkDetail() {
  const { workId = '' } = useParams(); const qc = useQueryClient()
  const query = useQuery({ queryKey: ['work', workId], queryFn: () => api<WorkItem>(`/api/work/${workId}`) })
  const pending = useQuery({ queryKey: ['pending-actions'], queryFn: () => api<{ actions: ActionOccurrence[] }>('/api/actions/pending') })
  const mutate = useMutation({ mutationFn: (action: string) => api(`/api/work/${workId}/${action}`, { method: 'POST', body: '{}' }), onSuccess: async () => { await qc.invalidateQueries({ queryKey: ['work', workId] }); await qc.invalidateQueries({ queryKey: ['work'] }); await qc.invalidateQueries({ queryKey: ['pending-actions'] }) } })
  const item = query.data
  return <Workspace title={item?.objective ?? 'Work'} crumb={<Link to="/work">Work</Link>} subtitle={item ? `Status: ${item.status}` : 'Loading…'} headerActions={<><button onClick={() => mutate.mutate('resume')}>Resume</button><button onClick={() => mutate.mutate('pause')}>Pause</button><button className="danger" onClick={() => mutate.mutate('cancel')}>Cancel</button></>}>
    <div className="stack">
      {(pending.data?.actions ?? []).filter(action => action.work_id === workId).map(action => <ConfirmationCard key={action.occurrence_id} item={action} onDone={async () => { await qc.invalidateQueries({ queryKey: ['pending-actions'] }); await qc.invalidateQueries({ queryKey: ['work', workId] }) }} />)}
      {(item?.steps ?? []).map(step => <Panel key={step.step_id} title={`Step ${step.ordinal} · ${step.status}`}><strong>{step.description}</strong><div className="meta">{step.capability_id}</div>{step.error ? <p className="offline-banner">{step.error}</p> : null}{step.output ? <pre className="mono" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(step.output, null, 2)}</pre> : null}</Panel>)}
    </div>
  </Workspace>
}
