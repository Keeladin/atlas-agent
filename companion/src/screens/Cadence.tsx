import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Cadence, Capability, WorkItem } from '../api/types'
import {
  FactList,
  InspectorPanel,
  InspectorSection,
  OperationalRibbon,
  StatusLamp,
} from '../ui/OperationsPrimitives'
import { cadenceStateToLamp, workStateToLamp } from '../ui/operationState'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Sheet } from '../ui/Sheet'
import { Workspace } from '../ui/Workspace'
import { OPERATIONS_TABS } from './operationsNav'

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
type StepDraft = { capability_id: string; input: string }

function scheduleLabel(schedule: Record<string, unknown>) {
  const kind = String(schedule.kind ?? '')
  if (kind === 'interval') return `Every ${Number(schedule.minutes ?? 0)} minutes`
  const time = `${String(schedule.hour ?? 8).padStart(2, '0')}:${String(schedule.minute ?? 0).padStart(2, '0')}`
  if (kind === 'weekly') return `${WEEKDAYS[Number(schedule.weekday ?? 0)] ?? 'Weekly'} at ${time}`
  return `Daily at ${time}`
}

function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

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
  const capabilities = useQuery({ queryKey: ['capabilities'], queryFn: () => api<{ capabilities: Capability[] }>('/api/capabilities') })
  const work = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work') })
  const [selectedId, setSelectedId] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState(''); const [objective, setObjective] = useState('')
  const [kind, setKind] = useState<'daily' | 'weekly' | 'interval'>('daily')
  const [hour, setHour] = useState(8); const [minute, setMinute] = useState(0); const [weekday, setWeekday] = useState(0); const [interval, setInterval] = useState(60)
  const [timezone, setTimezone] = useState('Africa/Johannesburg')
  const [steps, setSteps] = useState<StepDraft[]>([{ capability_id: 'knowledge.search', input: '{"query":"daily brief"}' }])
  const [formError, setFormError] = useState<string | null>(null)
  const create = useMutation({ mutationFn: (payload: object) => api('/api/cadence', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: async () => { setName(''); setObjective(''); setFormError(null); setCreateOpen(false); await qc.invalidateQueries({ queryKey: ['cadence'] }) } })
  const enable = useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api<Cadence>(`/api/cadence/${id}/enabled`, { method: 'POST', body: JSON.stringify({ enabled }) }), onSuccess: async item => { setSelectedId(item.cadence_id); await qc.invalidateQueries({ queryKey: ['cadence'] }) } })
  const remove = useMutation({ mutationFn: (id: string) => api(`/api/cadence/${id}`, { method: 'DELETE' }), onSuccess: async () => { setSelectedId(''); await qc.invalidateQueries({ queryKey: ['cadence'] }) } })

  function submit(event: FormEvent) {
    event.preventDefault(); setFormError(null)
    try {
      if (!name.trim() || !objective.trim()) throw new Error('Name and objective are required.')
      const schedule = kind === 'interval' ? { kind, minutes: interval, timezone } : kind === 'weekly' ? { kind, weekday, hour, minute, timezone } : { kind, hour, minute, timezone }
      const parsedSteps = steps.map(step => ({ capability_id: step.capability_id.trim(), input: JSON.parse(step.input || '{}') }))
      if (parsedSteps.some(step => !step.capability_id)) throw new Error('Every step needs a capability.')
      create.mutate({ name: name.trim(), objective: objective.trim(), schedule, steps: parsedSteps })
    } catch (error) { setFormError(error instanceof Error ? error.message : String(error)) }
  }

  const rows = useMemo(() => query.data?.cadences ?? [], [query.data?.cadences])
  useEffect(() => { if (!selectedId && rows.length) setSelectedId(rows[0].cadence_id) }, [rows, selectedId])
  const selected = rows.find(item => item.cadence_id === selectedId)
  const selectedWork = selected?.last_work_id ? (work.data?.work ?? []).find(item => item.work_id === selected.last_work_id) : undefined
  const days = useMemo(() => weekDays(), [])
  const enabledCount = rows.filter(item => item.enabled).length
  const disabledCount = rows.length - enabledCount
  const dueThisWeek = rows.filter(item => days.some(day => sameDay(item.next_run_at, day))).length
  const neverRun = rows.filter(item => !item.last_run_at).length
  const available = (capabilities.data?.capabilities ?? []).filter(item => item.available && !item.id.startsWith('cadence.'))

  const inspector = selected ? <InspectorPanel title={selected.name} eyebrow="Standing duty" status={<StatusLamp tone={cadenceStateToLamp(selected.enabled, selected.next_run_at)} label={selected.enabled ? 'enabled' : 'disabled'} />} actions={<>
    <button className={selected.enabled ? '' : 'confirm'} type="button" disabled={enable.isPending} onClick={() => enable.mutate({ id: selected.cadence_id, enabled: !selected.enabled })}>{selected.enabled ? 'Disable' : 'Enable'}</button>
    <button className="danger" type="button" disabled={remove.isPending} onClick={() => remove.mutate(selected.cadence_id)}>Delete</button>
  </>}>
    <p className="ops-inspector-objective">{selected.objective}</p>
    <InspectorSection title="Definition"><FactList items={[
      { label: 'Schedule', value: scheduleLabel(selected.schedule) },
      { label: 'Timezone', value: String(selected.schedule.timezone ?? '—'), mono: true },
      { label: 'Next run', value: when(selected.next_run_at), mono: true },
      { label: 'Last run', value: when(selected.last_run_at), mono: true },
    ]} /></InspectorSection>
    <InspectorSection title="Capability steps"><div className="ops-timeline compact">{selected.steps.map((step, index) => {
      const row = step as Record<string, unknown>
      return <div key={`${String(row.capability_id)}:${index}`}><span className="cadence-step-number">{index + 1}</span><span><strong>{String(row.description ?? row.capability_id ?? `Step ${index + 1}`)}</strong><small className="mono">{String(row.capability_id ?? '—')}</small></span></div>
    })}</div></InspectorSection>
    <InspectorSection title="Latest run">{selected.last_work_id ? <Link className="ops-linked-row" to={`/work/${selected.last_work_id}`}><span><strong className="mono">{selectedWork?.display_ref ?? selected.last_work_id}</strong><small>{selectedWork?.objective ?? when(selected.last_run_at)}</small></span><StatusLamp tone={selectedWork ? workStateToLamp(selectedWork.status) : 'dim'} label={selectedWork?.status.replaceAll('_', ' ') ?? 'Work reference'} /></Link> : <p className="meta">This cadence has not created Work yet.</p>}</InspectorSection>
    <p className="ops-authority-note"><StatusLamp tone="dim" /><span>Cadence creates ordinary governed Work. Every consequential step resolves current owner policy when it executes.</span></p>
    {enable.isError ? <p className="offline-banner">{enable.error.message}</p> : null}
    {remove.isError ? <p className="offline-banner">{remove.error.message}</p> : null}
  </InspectorPanel> : <InspectorPanel title="No standing duty selected" eyebrow="Cadence inspector"><div className="empty-state compact"><strong>Select a cadence</strong><span>Its definition, timing, steps, and latest Work will appear here.</span></div></InspectorPanel>

  return <Workspace className="operations-workspace" title="Cadence" subtitle="Recurring standing duties that instantiate ordinary Work." tabs={<SegmentedNav items={OPERATIONS_TABS} />} headerActions={<button className="primary" type="button" onClick={() => setCreateOpen(true)}>New standing duty</button>} context={inspector} contextLabel="Cadence details" banner={<OperationalRibbon items={[
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
      {!query.isLoading && !rows.length ? <div className="empty-state"><strong>No standing duties yet</strong><span>Create one when Atlas has Work that should recur on a predictable rhythm.</span></div> : null}
    </section>
    {createOpen ? <Sheet title="Create standing duty" onClose={() => setCreateOpen(false)}><form className="stack cadence-create-form" onSubmit={submit}>
      <label>Name<input value={name} onChange={event => setName(event.target.value)} placeholder="Monday engineering brief" /></label>
      <label>Objective<input value={objective} onChange={event => setObjective(event.target.value)} placeholder="Prepare the weekly engineering brief" /></label>
      <div className="cadence-schedule-grid">
        <label>Repeat<select value={kind} onChange={event => setKind(event.target.value as typeof kind)}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="interval">Interval</option></select></label>
        {kind === 'weekly' ? <label>Day<select value={weekday} onChange={event => setWeekday(Number(event.target.value))}>{WEEKDAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label> : null}
        {kind === 'interval' ? <label>Every (minutes)<input type="number" min={1} value={interval} onChange={event => setInterval(Number(event.target.value))} /></label> : <><label>Hour<input type="number" min={0} max={23} value={hour} onChange={event => setHour(Number(event.target.value))} /></label><label>Minute<input type="number" min={0} max={59} value={minute} onChange={event => setMinute(Number(event.target.value))} /></label></>}
        <label>Timezone<input value={timezone} onChange={event => setTimezone(event.target.value)} /></label>
      </div>
      <div className="cadence-step-head"><div><span className="eyebrow">Work steps</span><div className="meta">Deterministic capabilities Atlas will run in order.</div></div><button type="button" onClick={() => setSteps(current => [...current, { capability_id: available[0]?.id ?? '', input: '{}' }])}>Add step</button></div>
      <div className="stack">{steps.map((step, index) => <div className="cadence-step" key={index}><div className="row-head"><strong>Step {index + 1}</strong>{steps.length > 1 ? <button className="danger" type="button" onClick={() => setSteps(current => current.filter((_, row) => row !== index))}>Remove</button> : null}</div><label>Capability<select value={step.capability_id} onChange={event => setSteps(current => current.map((item, row) => row === index ? { ...item, capability_id: event.target.value } : item))}><option value={step.capability_id}>{step.capability_id || 'Select capability'}</option>{available.filter(item => item.id !== step.capability_id).map(item => <option key={item.id} value={item.id}>{item.id} — {item.description}</option>)}</select></label><details className="inspect"><summary>Input payload</summary><textarea className="mono" value={step.input} onChange={event => setSteps(current => current.map((item, row) => row === index ? { ...item, input: event.target.value } : item))} /></details></div>)}</div>
      {formError ? <p className="offline-banner">{formError}</p> : null}{create.isError ? <p className="offline-banner">{create.error.message}</p> : null}
      <button className="primary" type="submit" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create standing duty'}</button>
    </form></Sheet> : null}
  </Workspace>
}
