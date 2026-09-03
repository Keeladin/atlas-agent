import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionOccurrence, Cadence, WorkItem } from '../api/types'
import {
  FactList,
  InspectorPanel,
  InspectorSection,
  OperationalRibbon,
  StatusLamp,
} from '../ui/OperationsPrimitives'
import { cadenceStateToLamp, workStateToLamp } from '../ui/operationState'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace } from '../ui/Workspace'
import { focusQuery, readSteps, scheduleLabel, stepLabel, when } from '../ui/workflowPresentation'
import { OPERATIONS_TABS } from './operationsNav'

function weekDays() {
  const today = new Date(); today.setHours(0, 0, 0, 0)
  return Array.from({ length: 7 }, (_, index) => { const date = new Date(today); date.setDate(today.getDate() + index); return date })
}

function sameDay(value: string | null | undefined, day: Date) {
  if (!value) return false
  const date = new Date(value)
  return !Number.isNaN(date.getTime()) && date.getFullYear() === day.getFullYear() && date.getMonth() === day.getMonth() && date.getDate() === day.getDate()
}

export function CadenceList() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['cadence'], queryFn: () => api<{ cadences: Cadence[] }>('/api/cadence') })
  const [selectedId, setSelectedId] = useState('')
  const [message, setMessage] = useState<string | null>(null)

  const rows = useMemo(() => query.data?.cadences ?? [], [query.data?.cadences])
  useEffect(() => { if (!selectedId && rows.length) setSelectedId(rows[0].cadence_id) }, [rows, selectedId])
  const selected = rows.find(item => item.cadence_id === selectedId)

  const history = useQuery({
    queryKey: ['work', 'cadence', selectedId],
    queryFn: () => api<{ work: WorkItem[] }>(`/api/work?cadence_id=${encodeURIComponent(selectedId)}`),
    enabled: Boolean(selectedId),
  })

  async function refresh() {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ['cadence'] }),
      qc.invalidateQueries({ queryKey: ['work'] }),
    ])
  }

  const enable = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api<Cadence>(`/api/cadence/${id}/enabled`, { method: 'POST', body: JSON.stringify({ enabled }) }),
    onSuccess: async item => { setSelectedId(item.cadence_id); setMessage(null); await refresh() },
  })
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/cadence/${id}`, { method: 'DELETE' }),
    onSuccess: async () => { setSelectedId(''); setMessage(null); await refresh() },
  })
  const runNow = useMutation({
    mutationFn: (id: string) => api<{ action: ActionOccurrence }>('/api/capabilities/cadence.run_now/invoke', { method: 'POST', body: JSON.stringify({ input: { cadence_id: id } }) }),
    onSuccess: async ({ action }) => {
      setMessage(action.status === 'succeeded' ? 'Ran now. The schedule is unchanged.' : action.error || `Runtime returned ${action.status}.`)
      await refresh()
    },
    onError: error => setMessage(error instanceof Error ? error.message : String(error)),
  })

  const enabledCount = rows.filter(item => item.enabled).length
  const disabledCount = rows.length - enabledCount
  const days = useMemo(() => weekDays(), [])
  const dueThisWeek = rows.filter(item => days.some(day => sameDay(item.next_run_at, day))).length
  const neverRun = rows.filter(item => !item.last_run_at).length
  const historyRows = history.data?.work ?? []

  const inspector = selected ? <InspectorPanel title={selected.name} eyebrow="Standing duty" status={<StatusLamp tone={cadenceStateToLamp(selected.enabled, selected.next_run_at)} label={selected.enabled ? 'enabled' : 'disabled'} />} actions={<>
    <button type="button" disabled={runNow.isPending} onClick={() => runNow.mutate(selected.cadence_id)}>{runNow.isPending ? 'Running…' : 'Run now'}</button>
    <button className={selected.enabled ? '' : 'confirm'} type="button" disabled={enable.isPending} onClick={() => enable.mutate({ id: selected.cadence_id, enabled: !selected.enabled })}>{selected.enabled ? 'Disable' : 'Enable'}</button>
    <Link className="button-link primary" to={`/chat?${focusQuery({ cadence_id: selected.cadence_id })}&ask=${encodeURIComponent(`About the standing duty “${selected.name}” — `)}`}>Open in Chat</Link>
    <button className="danger" type="button" disabled={remove.isPending} onClick={() => remove.mutate(selected.cadence_id)}>Delete</button>
  </>}>
    <p className="ops-inspector-objective">{selected.objective}</p>
    <InspectorSection title="Definition"><FactList items={[
      { label: 'Kind', value: selected.kind === 'intake_sweep' ? 'Intake sweep' : 'Work template' },
      { label: 'Schedule', value: scheduleLabel(selected.schedule) },
      { label: 'Timezone', value: String(selected.schedule.timezone ?? '—'), mono: true },
      { label: 'Next run', value: when(selected.next_run_at), mono: true },
      { label: 'Last run', value: when(selected.last_run_at), mono: true },
    ]} /></InspectorSection>
    {selected.kind === 'work_template' ? <InspectorSection title="Capability steps"><div className="ops-timeline compact">{readSteps(selected.steps).map((step, index) => (
      <div key={`${step.capability_id}:${index}`}><span className="cadence-step-number">{index + 1}</span><span><strong>{stepLabel(step)}</strong><small className="mono">{step.capability_id}</small></span></div>
    ))}</div></InspectorSection> : <InspectorSection title="Monitored source"><FactList items={[
      { label: 'Root', value: String(selected.intake_root_id ?? '—'), mono: true },
      { label: 'Max candidates', value: selected.max_candidates ?? '—' },
    ]} /></InspectorSection>}
    <InspectorSection title="Run history">{historyRows.length ? <div className="stack compact">{historyRows.slice(0, 8).map(item => (
      <Link className="ops-linked-row" key={item.work_id} to={`/work/${item.work_id}`}>
        <span><strong className="mono">{item.display_ref ?? item.work_id}</strong><small>{when(item.created_at)}</small></span>
        <StatusLamp tone={workStateToLamp(item.status)} label={item.status.replaceAll('_', ' ')} />
      </Link>
    ))}</div> : <p className="meta">This cadence has not created Work yet.</p>}</InspectorSection>
    <p className="ops-authority-note"><StatusLamp tone="dim" /><span>Cadence creates ordinary governed Work. Every consequential step resolves current owner policy when it executes. Ask Atlas in Chat to change what this duty does.</span></p>
    {message ? <p className="meta">{message}</p> : null}
    {enable.isError ? <p className="offline-banner">{enable.error.message}</p> : null}
    {remove.isError ? <p className="offline-banner">{remove.error.message}</p> : null}
  </InspectorPanel> : <InspectorPanel title="No standing duty selected" eyebrow="Cadence inspector"><div className="empty-state compact"><strong>Select a cadence</strong><span>Its definition, timing, steps, and run history will appear here.</span></div></InspectorPanel>

  return <Workspace className="operations-workspace" title="Cadence" subtitle="Recurring standing duties that instantiate ordinary Work." tabs={<SegmentedNav items={OPERATIONS_TABS} />} headerActions={<Link className="button-link primary" to={`/chat?ask=${encodeURIComponent('Set up a new standing duty that ')}`}>Ask Atlas to add one</Link>} context={inspector} contextLabel="Cadence details" banner={<OperationalRibbon items={[
    { label: 'Enabled', value: enabledCount, tone: enabledCount ? 'green' : 'dim' },
    { label: 'Disabled', value: disabledCount, tone: 'dim' },
    { label: 'Due in 7 days', value: dueThisWeek, tone: dueThisWeek ? 'amber' : 'dim' },
    { label: 'Never run', value: neverRun, tone: 'dim' },
  ]} />}>
    <section className="ops-surface cadence-surface" aria-label="Standing duties">
      <div className="cadence-ribbon" aria-label="Upcoming seven days">{days.map((day, index) => {
        const count = rows.filter(item => sameDay(item.next_run_at, day)).length
        return <div className={index === 0 ? 'today' : ''} key={day.toISOString()}><span>{day.toLocaleDateString(undefined, { weekday: 'short' })}</span><strong>{day.getDate()}</strong><StatusLamp tone={count ? 'amber' : 'dim'} label={count ? String(count) : undefined} /></div>
      })}</div>
      <div className="cadence-table">
        <div className="cadence-table-head"><span>Standing duty</span><span>Schedule</span><span>Next run</span><span>Last run</span><span>Latest Work</span><span>State</span></div>
        <div className="cadence-table-body">{rows.map(item => <button type="button" className={`cadence-table-row ${item.cadence_id === selectedId ? 'active' : ''}`} key={item.cadence_id} onClick={() => setSelectedId(item.cadence_id)}>
          <span className="cadence-table-name"><StatusLamp tone={cadenceStateToLamp(item.enabled, item.next_run_at)} /><span><strong>{item.name}</strong><small>{item.objective}</small></span></span>
          <span>{scheduleLabel(item.schedule)}</span><time className="mono">{when(item.next_run_at)}</time><time className="mono">{when(item.last_run_at)}</time><span className="mono">{item.last_work_id ?? '—'}</span><span className={`chip ${item.enabled ? 'done' : ''}`}>{item.enabled ? 'enabled' : 'disabled'}</span>
        </button>)}</div>
      </div>
      {query.isError ? <p className="offline-banner">{query.error.message}</p> : null}
      {!query.isLoading && !rows.length ? <div className="empty-state"><strong>No standing duties yet</strong><span>Describe one to Atlas in Chat — “every weekday morning, check my calendar and summarize what’s coming up.”</span></div> : null}
    </section>
  </Workspace>
}
