import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkItem, WorkStep } from '../api/types'
import {
  FactList,
  InspectorPanel,
  InspectorSection,
  OperationalRibbon,
  StatusLamp,
} from '../ui/OperationsPrimitives'
import { workStateToLamp } from '../ui/operationState'
import { focusQuery } from '../ui/workflowPresentation'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace, WorkspaceRailSection } from '../ui/Workspace'
import { OPERATIONS_TABS } from './operationsNav'

type WorkFilter = 'all' | 'active' | 'waiting' | 'paused' | 'failed' | 'completed'

function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function currentStep(item?: WorkItem): WorkStep | undefined {
  return item?.steps?.find(step => !['completed', 'cancelled'].includes(step.status)) ?? item?.steps?.at(-1)
}

function matchesFilter(item: WorkItem, filter: WorkFilter) {
  if (filter === 'all') return true
  if (filter === 'active') return ['active', 'running'].includes(item.status)
  if (filter === 'waiting') return item.status === 'waiting'
  return item.status === filter
}

export function WorkList() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work') })
  const [filter, setFilter] = useState<WorkFilter>('all')
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const control = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'resume' | 'pause' | 'cancel' }) => api<WorkItem>(`/api/work/${id}/${action}`, { method: 'POST', body: '{}' }),
    onSuccess: async item => { setSelectedId(item.work_id); await qc.invalidateQueries({ queryKey: ['work'] }) },
  })
  const rows = useMemo(() => query.data?.work ?? [], [query.data?.work])
  useEffect(() => { if (!selectedId && rows.length) setSelectedId(rows[0].work_id) }, [rows, selectedId])
  const selected = rows.find(item => item.work_id === selectedId)
  const filteredRows = useMemo(() => rows.filter(item => matchesFilter(item, filter) && (!search || `${item.display_ref ?? ''} ${item.objective} ${item.workflow_class ?? ''}`.toLowerCase().includes(search.toLowerCase()))), [filter, rows, search])
  const open = rows.filter(item => !['completed', 'cancelled', 'failed'].includes(item.status)).length
  const waiting = rows.filter(item => ['waiting', 'paused'].includes(item.status)).length
  const completed = rows.filter(item => item.status === 'completed').length
  const failed = rows.filter(item => item.status === 'failed').length
  const completedSteps = selected?.steps?.filter(step => step.status === 'completed').length ?? 0
  const totalSteps = selected?.steps?.length ?? 0
  const step = currentStep(selected)
  const terminal = selected ? ['completed', 'cancelled'].includes(selected.status) : true
  const canResume = Boolean(selected && !terminal && ['paused', 'waiting', 'failed'].includes(selected.status))
  const canPause = Boolean(selected && !terminal && selected.status !== 'paused')

  const rail = <div className="ops-rail-panel">
    <WorkspaceRailSection title="Queue filters">
      <div className="ops-filter-stack">{(['all', 'active', 'waiting', 'paused', 'failed', 'completed'] as WorkFilter[]).map(value => <button type="button" className={filter === value ? 'active' : ''} key={value} onClick={() => setFilter(value)}><span>{value}</span><strong>{value === 'all' ? rows.length : rows.filter(item => matchesFilter(item, value)).length}</strong></button>)}</div>
    </WorkspaceRailSection>
    <WorkspaceRailSection title="Search"><input aria-label="Search Work" value={search} onChange={event => setSearch(event.target.value)} placeholder="Ref, objective, workflow…" /></WorkspaceRailSection>
    <WorkspaceRailSection title="New Work">
      <p className="meta">Describe what Atlas should own until it is done. Atlas builds the capability steps and creates the Work.</p>
      <Link className="button-link primary" to={`/chat?ask=${encodeURIComponent('Take this on as Work: ')}`}>Ask Atlas in Chat</Link>
    </WorkspaceRailSection>
  </div>

  const inspector = selected ? <InspectorPanel title={selected.objective} eyebrow={selected.display_ref ?? selected.work_id} status={<StatusLamp tone={workStateToLamp(selected.status)} label={selected.status.replaceAll('_', ' ')} />} actions={<>
    {canResume ? <button className="confirm" type="button" disabled={control.isPending} onClick={() => control.mutate({ id: selected.work_id, action: 'resume' })}>{selected.status === 'failed' ? 'Retry' : 'Resume'}</button> : null}
    {canPause ? <button type="button" disabled={control.isPending} onClick={() => control.mutate({ id: selected.work_id, action: 'pause' })}>Pause</button> : null}
    {!terminal ? <button className="danger" type="button" disabled={control.isPending} onClick={() => control.mutate({ id: selected.work_id, action: 'cancel' })}>Cancel</button> : null}
    <Link className="button-link" to={`/chat?${focusQuery({ work_id: selected.work_id })}&ask=${encodeURIComponent(`About the Work “${selected.objective}” — `)}`}>Open in Chat</Link>
    <Link className="button-link primary" to={`/work/${selected.work_id}`}>Open full Work</Link>
  </>}>
    <InspectorSection title="Responsibility"><FactList items={[
      { label: 'Owner', value: selected.owner_principal_id, mono: true },
      { label: 'Workflow', value: String(selected.metadata?.workflow_intent ?? selected.workflow_class ?? 'runtime') },
      { label: 'Created', value: when(selected.created_at), mono: true },
      { label: 'Updated', value: when(selected.updated_at), mono: true },
      { label: 'Steps', value: totalSteps ? `${completedSteps} of ${totalSteps} completed` : 'No steps' },
    ]} /></InspectorSection>
    {step ? <InspectorSection title="Current step"><div className="ops-current-step"><StatusLamp tone={workStateToLamp(step.status)} /><span><strong>{step.description}</strong><small className="mono">{step.capability_id}</small><small>{step.status.replaceAll('_', ' ')}</small></span></div>{step.error ? <p className="offline-banner">{step.error}</p> : null}</InspectorSection> : null}
    {(selected.metadata?.source_artifact_id || selected.metadata?.artifact_id) ? <InspectorSection title="Linked artifacts"><FactList items={[
      { label: 'Source artifact', value: String(selected.metadata?.source_artifact_id ?? '—'), mono: true },
      { label: 'Managed artifact', value: String(selected.metadata?.artifact_id ?? '—'), mono: true },
    ]} /></InspectorSection> : null}
    {control.isError ? <p className="offline-banner">{control.error.message}</p> : null}
  </InspectorPanel> : <InspectorPanel title="No responsibility selected" eyebrow="Work inspector"><div className="empty-state compact"><strong>Select Work</strong><span>Responsibility facts and available runtime actions will remain visible here.</span></div></InspectorPanel>

  return <Workspace className="operations-workspace" title="Work" subtitle="Durable responsibilities created by the runtime and carried to verified completion." tabs={<SegmentedNav items={OPERATIONS_TABS} />} rail={rail} railLabel="Queue and filters" context={inspector} contextLabel="Work details" banner={<OperationalRibbon items={[
    { label: 'Open', value: open, tone: open ? 'amber' : 'dim' },
    { label: 'Waiting / paused', value: waiting, tone: waiting ? 'amber' : 'dim' },
    { label: 'Failed', value: failed, tone: failed ? 'red' : 'dim' },
    { label: 'Completed', value: completed, tone: completed ? 'green' : 'dim' },
  ]} />}>
    <section className="ops-surface work-queue" aria-label="Responsibility queue">
      <header className="ops-surface-head"><div><span className="eyebrow">Responsibility queue</span><strong>{filteredRows.length} shown</strong></div><span className="meta">Select a row to inspect runtime truth and available actions.</span></header>
      {query.isError ? <p className="offline-banner">{query.error.message}</p> : null}
      <div className="work-table">
        <div className="work-table-head"><span>Ref / objective</span><span>Current step</span><span>Updated</span><span>Steps</span><span>Status</span></div>
        <div className="work-table-body">{filteredRows.map(item => {
          const itemStep = currentStep(item)
          const done = item.steps?.filter(value => value.status === 'completed').length ?? 0
          const total = item.steps?.length ?? 0
          return <button type="button" className={`work-table-row ${item.work_id === selectedId ? 'active' : ''}`} key={item.work_id} onClick={() => setSelectedId(item.work_id)}>
            <span className="work-table-objective"><StatusLamp tone={workStateToLamp(item.status)} /><span><strong className="mono">{item.display_ref ?? item.work_id}</strong><small>{item.objective}</small></span></span>
            <span><strong>{itemStep?.description ?? 'No current step'}</strong><small className="mono">{itemStep?.capability_id ?? String(item.metadata?.workflow_intent ?? item.workflow_class ?? 'runtime')}</small></span>
            <time className="mono">{when(item.updated_at)}</time><span className="mono">{total ? `${done} / ${total}` : '—'}</span><span className={`chip ${item.status === 'completed' ? 'done' : item.status === 'failed' ? 'failed' : 'running'}`}>{item.status.replaceAll('_', ' ')}</span>
          </button>
        })}</div>
      </div>
      {!filteredRows.length && !query.isLoading ? <div className="empty-state"><strong>No matching Work</strong><span>Change the queue filter or search terms.</span></div> : null}
    </section>
  </Workspace>
}
