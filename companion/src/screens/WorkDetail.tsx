import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionOccurrence, WorkItem } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { Panel } from '../ui/Panel'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace } from '../ui/Workspace'
import { OPERATIONS_TABS } from './operationsNav'

function stateClass(status: string) {
  if (status === 'completed') return 'done'
  if (status === 'active' || status === 'running') return 'running'
  if (status === 'waiting_confirmation') return 'confirm'
  if (status === 'failed' || status === 'cancelled') return 'failed'
  return ''
}

export function WorkDetail() {
  const { workId = '' } = useParams(); const qc = useQueryClient()
  const query = useQuery({ queryKey: ['work', workId], queryFn: () => api<WorkItem>(`/api/work/${workId}`) })
  const pending = useQuery({ queryKey: ['pending-actions'], queryFn: () => api<{ actions: ActionOccurrence[] }>('/api/actions/pending') })
  const mutate = useMutation({ mutationFn: (action: string) => api(`/api/work/${workId}/${action}`, { method: 'POST', body: '{}' }), onSuccess: async () => { await qc.invalidateQueries({ queryKey: ['work', workId] }); await qc.invalidateQueries({ queryKey: ['work'] }); await qc.invalidateQueries({ queryKey: ['pending-actions'] }) } })
  const item = query.data
  const completed = item?.steps?.filter(step => step.status === 'completed').length ?? 0
  const total = item?.steps?.length ?? 0
  return <Workspace
    title={item?.display_ref ? `${item.display_ref} · ${item.objective}` : item?.objective ?? 'Work'}
    crumb={<Link to="/work">Work</Link>}
    subtitle={item ? `${item.status.replaceAll('_', ' ')} · ${String(item.metadata?.workflow_intent ?? item.workflow_class ?? 'runtime responsibility')}` : 'Loading…'}
    tabs={<SegmentedNav items={OPERATIONS_TABS} />}
    headerActions={<><button onClick={() => mutate.mutate('resume')}>Resume</button><button onClick={() => mutate.mutate('pause')}>Pause</button><button className="danger" onClick={() => mutate.mutate('cancel')}>Cancel</button></>}
  >
    <div className="work-operator-stack">
      {(pending.data?.actions ?? []).filter(action => action.work_id === workId).map(action => <ConfirmationCard key={action.occurrence_id} item={action} onDone={async () => { await qc.invalidateQueries({ queryKey: ['pending-actions'] }); await qc.invalidateQueries({ queryKey: ['work', workId] }) }} />)}

      {item ? <Panel className="work-state-panel" title="Responsibility state">
        <div className="work-state-head"><div><span className="eyebrow">{item.display_ref ?? item.work_id}</span><h2>{item.objective}</h2></div><span className={`chip ${stateClass(item.status)}`}>{item.status.replaceAll('_', ' ')}</span></div>
        <div className="work-runtime-grid">
          <div className="work-fact-list"><div><span>Workflow</span><strong>{String(item.metadata?.workflow_intent ?? item.workflow_class ?? '—')}</strong></div><div><span>Artifact class</span><strong>{item.artifact_class ?? '—'}</strong></div></div>
          <div className="work-fact-list"><div><span>Managed artifact</span><strong className="mono">{String(item.metadata?.artifact_id ?? '—')}</strong></div><div><span>Source artifact</span><strong className="mono">{String(item.metadata?.source_artifact_id ?? '—')}</strong></div></div>
          <div className="work-fact-list"><div><span>Progress</span><strong>{completed} / {total} steps complete</strong></div><div><span>Updated</span><strong>{item.updated_at}</strong></div></div>
        </div>
      </Panel> : null}
      <Panel title="Execution">
        <div className="work-execution-list">{(item?.steps ?? []).map(step => <article className="work-execution-step" key={step.step_id}>
          <div className="work-step-marker"><span>{step.ordinal}</span></div>
          <div className="work-step-body"><div className="row-head"><div><strong>{step.description}</strong><div className="meta mono">{step.capability_id}</div></div><span className={`chip ${stateClass(step.status)}`}>{step.status.replaceAll('_', ' ')}</span></div>
          {step.error ? <p className="offline-banner">{step.error}</p> : null}
          {step.output ? <details className="inspect"><summary>Evidence / output</summary><pre className="mono">{JSON.stringify(step.output, null, 2)}</pre></details> : null}
          </div>
        </article>)}</div>
      </Panel>

      {item ? <details className="inspect"><summary>Technical Work record</summary><pre className="mono">{JSON.stringify(item, null, 2)}</pre></details> : null}
    </div>
  </Workspace>
}
