import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionOccurrence, WorkItem } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import {
  FactList,
  InspectorPanel,
  InspectorSection,
  OperationalRibbon,
  StatusLamp,
} from '../ui/OperationsPrimitives'
import { workStateToLamp } from '../ui/operationState'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace, WorkspaceRailSection } from '../ui/Workspace'
import { OPERATIONS_TABS } from './operationsNav'

function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

export function WorkDetail() {
  const { workId = '' } = useParams(); const qc = useQueryClient()
  const query = useQuery({ queryKey: ['work', workId], queryFn: () => api<WorkItem>(`/api/work/${workId}`) })
  const pending = useQuery({ queryKey: ['pending-actions'], queryFn: () => api<{ actions: ActionOccurrence[] }>('/api/actions/pending') })
  const mutate = useMutation({ mutationFn: (action: string) => api<WorkItem>(`/api/work/${workId}/${action}`, { method: 'POST', body: '{}' }), onSuccess: async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['work', workId] }), qc.invalidateQueries({ queryKey: ['work'] }), qc.invalidateQueries({ queryKey: ['pending-actions'] })]) } })
  const item = query.data
  const itemPending = (pending.data?.actions ?? []).filter(action => action.work_id === workId)
  const completed = item?.steps?.filter(step => step.status === 'completed').length ?? 0
  const total = item?.steps?.length ?? 0
  const current = item?.steps?.find(step => !['completed', 'cancelled'].includes(step.status))
  const terminal = !item || ['completed', 'cancelled'].includes(item.status)
  const canResume = Boolean(item && !terminal && ['paused', 'waiting', 'failed'].includes(item.status))
  const canPause = Boolean(item && !terminal && !['paused', 'waiting_confirmation'].includes(item.status))

  const facts = item ? <div className="ops-rail-panel"><WorkspaceRailSection title="Responsibility facts"><FactList items={[
    { label: 'Reference', value: item.display_ref ?? item.work_id, mono: true },
    { label: 'Owner', value: item.owner_principal_id, mono: true },
    { label: 'Workflow intent', value: String(item.metadata?.workflow_intent ?? item.workflow_class ?? '—') },
    { label: 'Artifact class', value: item.artifact_class ?? '—' },
    { label: 'Created', value: when(item.created_at), mono: true },
    { label: 'Updated', value: when(item.updated_at), mono: true },
    { label: 'Step state', value: total ? `${completed} of ${total} completed` : 'No steps' },
  ]} /></WorkspaceRailSection><WorkspaceRailSection title="Linked runtime"><FactList items={[
    { label: 'Source artifact', value: String(item.metadata?.source_artifact_id ?? '—'), mono: true },
    { label: 'Managed artifact', value: String(item.metadata?.artifact_id ?? '—'), mono: true },
  ]} /></WorkspaceRailSection><WorkspaceRailSection title="Authority"><p className="ops-authority-note"><StatusLamp tone={itemPending.length ? 'red' : 'dim'} /><span>Capability is not authority. Every consequential step resolves current owner policy at execution time.</span></p></WorkspaceRailSection></div> : <div className="empty-state compact">Loading…</div>

  const actionPanel = item ? <InspectorPanel title={itemPending.length ? 'Exact action required' : 'Available action'} eyebrow="Authority / action" status={<StatusLamp tone={itemPending.length ? 'red' : workStateToLamp(item.status)} label={itemPending.length ? 'confirmation required' : item.status.replaceAll('_', ' ')} />} actions={<>
    {canResume ? <button className="confirm" disabled={mutate.isPending} onClick={() => mutate.mutate('resume')}>{item.status === 'failed' ? 'Retry' : 'Resume'}</button> : null}
    {canPause ? <button disabled={mutate.isPending} onClick={() => mutate.mutate('pause')}>Pause</button> : null}
    {!terminal ? <button className="danger" disabled={mutate.isPending} onClick={() => mutate.mutate('cancel')}>Cancel Work</button> : null}
  </>}>
    {itemPending.map(action => <ConfirmationCard key={action.occurrence_id} item={action} onDone={async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['pending-actions'] }), qc.invalidateQueries({ queryKey: ['work', workId] })]) }} />)}
    {!itemPending.length ? <InspectorSection title="Runtime state"><FactList items={[
      { label: 'Status', value: item.status.replaceAll('_', ' ') },
      { label: 'Current step', value: current?.description ?? 'No pending step' },
      { label: 'Capability', value: current?.capability_id ?? '—', mono: true },
      { label: 'Occurrence', value: current?.occurrence_id ?? '—', mono: true },
    ]} />{current?.error ? <p className="offline-banner">{current.error}</p> : null}</InspectorSection> : null}
    {mutate.isError ? <p className="offline-banner">{mutate.error.message}</p> : null}
  </InspectorPanel> : <InspectorPanel title="Loading Work" eyebrow="Authority / action"><div className="empty-state compact">Loading…</div></InspectorPanel>

  return <Workspace className="operations-workspace work-detail-workspace" title={item?.objective ?? 'Work'} crumb={<Link to="/work">← Work queue</Link>} subtitle={item ? `${item.display_ref ?? item.work_id} · ${item.status.replaceAll('_', ' ')}` : 'Loading…'} tabs={<SegmentedNav items={OPERATIONS_TABS} />} rail={facts} railLabel="Responsibility facts" context={actionPanel} contextLabel={itemPending.length ? 'Action required' : 'Work actions'} banner={item ? <OperationalRibbon items={[
    { label: 'Status', value: item.status.replaceAll('_', ' '), tone: workStateToLamp(item.status) },
    { label: 'Completed steps', value: `${completed} / ${total}`, tone: total && completed === total ? 'green' : total ? 'amber' : 'dim' },
    { label: 'Current capability', value: current?.capability_id ?? 'None', tone: current ? workStateToLamp(current.status) : 'dim' },
    { label: 'Confirmation', value: itemPending.length ? `${itemPending.length} pending` : 'None pending', tone: itemPending.length ? 'red' : 'dim' },
  ]} /> : undefined}>
    <section className="ops-surface execution-surface" aria-label="Execution timeline">
      <header className="ops-surface-head"><div><span className="eyebrow">Execution</span><strong>Capability timeline</strong></div><span className="meta">Evidence and output remain attached to the step that produced them.</span></header>
      {query.isError ? <p className="offline-banner">{query.error.message}</p> : null}
      <div className="execution-timeline">{(item?.steps ?? []).map(step => <article className={`execution-step ${step.status}`} key={step.step_id}>
        <div className="execution-marker"><span>{step.ordinal}</span><StatusLamp tone={workStateToLamp(step.status)} /></div>
        <div className="execution-step-body"><div className="execution-step-head"><div><strong>{step.description}</strong><span className="mono">{step.capability_id}</span></div><span className={`chip ${step.status === 'completed' ? 'done' : step.status === 'failed' || step.status === 'waiting_confirmation' ? 'failed' : 'running'}`}>{step.status.replaceAll('_', ' ')}</span></div>
          <div className="execution-step-meta"><span>Step <strong className="mono">{step.ordinal}</strong></span><span>Occurrence <strong className="mono">{step.occurrence_id ?? '—'}</strong></span></div>
          {step.error ? <p className="offline-banner">{step.error}</p> : null}
          {step.output ? <details className="inspect"><summary>Evidence / output</summary><pre className="mono">{JSON.stringify(step.output, null, 2)}</pre></details> : null}
        </div>
      </article>)}</div>
      {!item?.steps?.length && !query.isLoading ? <div className="empty-state"><strong>No execution steps</strong></div> : null}
      {item ? <details className="inspect technical-work-record"><summary>Technical Work record</summary><pre className="mono">{JSON.stringify(item, null, 2)}</pre></details> : null}
    </section>
  </Workspace>
}
