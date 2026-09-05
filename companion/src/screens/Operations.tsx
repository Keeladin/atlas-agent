import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { AttentionItem, Cadence, SourceRoot, WorkItem } from '../api/types'
import {
  FactList,
  InspectorPanel,
  InspectorSection,
  OperationalRibbon,
  OperationalRow,
  StatusLamp,
} from '../ui/OperationsPrimitives'
import { cadenceStateToLamp, runtimeStateToLamp, workStateToLamp } from '../ui/operationState'
import { attentionDetail, attentionHref, attentionStatus, attentionTitle, attentionTone } from '../ui/attentionState'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace } from '../ui/Workspace'
import { OPERATIONS_TABS } from './operationsNav'

type Artifact = { artifact_id: string; display_name: string; managed_content?: Record<string, unknown>; managed_representations?: Array<Record<string, unknown>> }
type LibraryScan = { scan_id: string; status: string; summary: Record<string, number>; error?: string | null; created_at: string; completed_at?: string | null }

function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

export function Operations() {
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<{ ok: boolean; service: string; version: string }>('/api/health') })
  const roots = useQuery({ queryKey: ['source-roots'], queryFn: () => api<{ roots: SourceRoot[] }>('/api/sources/roots') })
  const work = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work') })
  const artifacts = useQuery({ queryKey: ['artifacts'], queryFn: () => api<{ artifacts: Artifact[] }>('/api/artifacts') })
  const cadence = useQuery({ queryKey: ['cadence'], queryFn: () => api<{ cadences: Cadence[] }>('/api/cadence') })
  const scans = useQuery({ queryKey: ['library-scans'], queryFn: () => api<{ scans: LibraryScan[] }>('/api/library/scans') })
  const attention = useQuery({ queryKey: ['attention'], queryFn: () => api<{ attention: AttentionItem[] }>('/api/attention'), refetchInterval: 5000 })

  const rootRows = (roots.data?.roots ?? []).filter(root => root.enabled)
  const workRows = work.data?.work ?? []
  const artifactRows = artifacts.data?.artifacts ?? []
  const cadenceRows = cadence.data?.cadences ?? []
  const scanRows = scans.data?.scans ?? []
  const activeWork = workRows.filter(item => ['active', 'running'].includes(item.status))
  const waitingWork = workRows.filter(item => ['waiting', 'paused'].includes(item.status))
  const enabledCadence = cadenceRows.filter(item => item.enabled)
  const upcoming = enabledCadence.filter(item => item.next_run_at).sort((a, b) => String(a.next_run_at).localeCompare(String(b.next_run_at))).slice(0, 5)
  const managed = artifactRows.filter(item => item.managed_content).length
  const changedWork = [...workRows].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 5)
  const runtimeTone = health.isError ? 'red' : health.data?.ok ? 'green' : 'dim'
  const runtimeLabel = health.isError ? 'Unavailable' : health.data?.ok ? 'Available' : 'Checking'

  const attentionItems = (attention.data?.attention ?? []).map(item => ({
    key: `${item.kind}:${item.obligation_id}`,
    lamp: attentionTone(item),
    title: attentionTitle(item),
    detail: attentionDetail(item),
    status: attentionStatus(item),
    to: attentionHref(item),
  }))

  const pulse = <InspectorPanel title="Operational pulse" eyebrow="Live runtime truth" status={<StatusLamp tone={runtimeTone} label={runtimeLabel} />}>
    <InspectorSection title="Runtime"><FactList items={[
      { label: 'Service', value: health.data?.service ?? 'atlas-api', mono: true },
      { label: 'Version', value: health.data?.version ?? '—', mono: true },
      { label: 'Status', value: runtimeLabel },
    ]} /></InspectorSection>
    <InspectorSection title="Operations"><FactList items={[
      { label: 'Enrolled sources', value: rootRows.length },
      { label: 'Observed artifacts', value: artifactRows.length },
      { label: 'Managed artifacts', value: managed },
      { label: 'Enabled cadence', value: enabledCadence.length },
    ]} /></InspectorSection>
    <InspectorSection title="Upcoming cadence"><div className="ops-compact-list">{upcoming.map(item => <Link className="ops-linked-row" to="/cadence" key={item.cadence_id}><span><strong>{item.name}</strong><small className="mono">{when(item.next_run_at)}</small></span><StatusLamp tone={cadenceStateToLamp(item.enabled, item.next_run_at)} /></Link>)}{!upcoming.length ? <p className="meta">No enabled cadence has a known next run.</p> : null}</div></InspectorSection>
    <InspectorSection title="Recent changes"><div className="ops-compact-list">{changedWork.map(item => <Link className="ops-linked-row" to={`/work/${item.work_id}`} key={item.work_id}><span><strong>{item.display_ref ?? item.work_id}</strong><small>{item.objective}</small><small className="mono">{when(item.updated_at)}</small></span><StatusLamp tone={workStateToLamp(item.status)} /></Link>)}{!changedWork.length ? <p className="meta">No Work changes recorded.</p> : null}</div></InspectorSection>
  </InspectorPanel>

  return <Workspace className="operations-workspace overview-workspace" title="Operations" subtitle="What is true, what needs attention, and what can be acted on now." tabs={<SegmentedNav items={OPERATIONS_TABS} />} context={pulse} contextLabel="Operational pulse" banner={<OperationalRibbon items={[
    { label: 'Needs attention', value: attentionItems.length, tone: attentionItems.length ? 'red' : 'dim' },
    { label: 'Active Work', value: activeWork.length, tone: activeWork.length ? 'amber' : 'dim' },
    { label: 'Waiting / paused', value: waitingWork.length, tone: waitingWork.length ? 'amber' : 'dim' },
    { label: 'Upcoming cadence', value: upcoming.length, tone: upcoming.length ? 'green' : 'dim' },
    { label: 'Runtime', value: runtimeLabel, tone: runtimeTone },
  ]} />}>
    <div className="overview-grid">
      <section className="ops-surface overview-attention" aria-label="Needs attention"><header className="ops-surface-head"><div><span className="eyebrow">Needs attention</span><strong>{attentionItems.length ? `${attentionItems.length} actionable items` : 'Nothing requires owner action'}</strong></div></header><div className="ops-attention-list">{attentionItems.slice(0, 6).map(item => <Link to={item.to} key={`${item.key}:${item.status}`}><OperationalRow lamp={item.lamp} label={item.title} secondary={item.detail} status={<span className={`chip ${item.lamp === 'red' ? 'failed' : 'running'}`}>{item.status}</span>} /></Link>)}{!attentionItems.length ? <div className="empty-state compact"><strong>No open obligation currently requires owner attention.</strong></div> : null}</div></section>
      <section className="ops-surface overview-active" aria-label="Active responsibilities"><header className="ops-surface-head"><div><span className="eyebrow">Currently running</span><strong>Active responsibilities</strong></div><Link to="/work">View all Work →</Link></header><div className="overview-work-list">{activeWork.slice(0, 10).map(item => {
        const completed = item.steps?.filter(step => step.status === 'completed').length ?? 0
        const total = item.steps?.length ?? 0
        const step = item.steps?.find(value => value.status !== 'completed')
        return <Link to={`/work/${item.work_id}`} key={item.work_id}><OperationalRow lamp={workStateToLamp(item.status)} label={item.objective} secondary={<><span className="mono">{item.display_ref ?? item.work_id}</span> · {step?.description ?? String(item.metadata?.workflow_intent ?? item.workflow_class ?? 'runtime')}</>} meta={total ? `${completed} / ${total} steps` : 'No step count'} status={<span className="chip running">{item.status}</span>} /></Link>
      })}{!activeWork.length ? <div className="empty-state compact"><strong>No active Work</strong><span>Waiting, paused, failed, and completed responsibilities remain available in Work.</span></div> : null}</div></section>
      <section className="ops-surface overview-events" aria-label="Operational events"><header className="ops-surface-head"><div><span className="eyebrow">Recent significant events</span><strong>Source and Work state changes</strong></div></header><div className="ops-compact-list">{scanRows.slice(0, 3).map(scan => <Link className="ops-linked-row" to="/sources" key={scan.scan_id}><span><strong>Library scan {scan.status}</strong><small className="mono">{scan.scan_id} · {when(scan.completed_at ?? scan.created_at)}</small></span><StatusLamp tone={runtimeStateToLamp(scan.status)} /></Link>)}{changedWork.slice(0, 3).map(item => <Link className="ops-linked-row" to={`/work/${item.work_id}`} key={item.work_id}><span><strong>{item.objective}</strong><small className="mono">{item.display_ref ?? item.work_id} · {when(item.updated_at)}</small></span><StatusLamp tone={workStateToLamp(item.status)} label={item.status.replaceAll('_', ' ')} /></Link>)}</div></section>
    </div>
  </Workspace>
}
