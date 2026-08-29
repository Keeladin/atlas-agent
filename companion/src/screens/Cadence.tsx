import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { Cadence, Capability } from '../api/types'
import { Panel } from '../ui/Panel'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace } from '../ui/Workspace'

const TABS = [{ to: '/work', label: 'Work', end: true }, { to: '/cadence', label: 'Cadence', end: true }]
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

export function CadenceList() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['cadence'], queryFn: () => api<{ cadences: Cadence[] }>('/api/cadence') })
  const capabilities = useQuery({ queryKey: ['capabilities'], queryFn: () => api<{ capabilities: Capability[] }>('/api/capabilities') })
  const [name, setName] = useState('')
  const [objective, setObjective] = useState('')
  const [kind, setKind] = useState<'daily' | 'weekly' | 'interval'>('daily')
  const [hour, setHour] = useState(8); const [minute, setMinute] = useState(0); const [weekday, setWeekday] = useState(0); const [interval, setInterval] = useState(60)
  const [timezone, setTimezone] = useState('Africa/Johannesburg')
  const [steps, setSteps] = useState<StepDraft[]>([{ capability_id: 'knowledge.search', input: '{"query":"daily brief"}' }])
  const [formError, setFormError] = useState<string | null>(null)
  const create = useMutation({ mutationFn: (payload: object) => api('/api/cadence', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: async () => { setName(''); setObjective(''); setFormError(null); await qc.invalidateQueries({ queryKey: ['cadence'] }) } })
  const enable = useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api(`/api/cadence/${id}/enabled`, { method: 'POST', body: JSON.stringify({ enabled }) }), onSuccess: () => qc.invalidateQueries({ queryKey: ['cadence'] }) })
  const remove = useMutation({ mutationFn: (id: string) => api(`/api/cadence/${id}`, { method: 'DELETE' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['cadence'] }) })

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

  const available = (capabilities.data?.capabilities ?? []).filter(item => item.available && !item.id.startsWith('cadence.'))
  return <Workspace title="Cadence" subtitle="Recurring standing duties that instantiate ordinary Work." tabs={<SegmentedNav items={TABS} />}>
    <div className="cadence-layout">
      <Panel title="Standing duties">
        {query.isError ? <p className="offline-banner">{query.error.message}</p> : null}
        {!query.isLoading && !(query.data?.cadences.length) ? <div className="empty-state"><strong>No standing duties yet</strong><span>Create one when Atlas has work that should recur on a predictable rhythm.</span></div> : null}
        <div className="stack">{(query.data?.cadences ?? []).map(item => <div className="cadence-item" key={item.cadence_id}>
          <div className="row-head"><div><strong>{item.name}</strong><div className="meta">{item.objective}</div></div><span className={`chip ${item.enabled ? 'done' : ''}`}>{item.enabled ? 'enabled' : 'disabled'}</span></div>
          <div className="cadence-facts"><div><span>Schedule</span><strong>{scheduleLabel(item.schedule)}</strong></div><div><span>Next run</span><strong>{when(item.next_run_at)}</strong></div><div><span>Last run</span><strong>{when(item.last_run_at)}</strong></div></div>
          <div className="actions"><button onClick={() => enable.mutate({ id: item.cadence_id, enabled: !item.enabled })}>{item.enabled ? 'Disable' : 'Enable'}</button><button className="danger" onClick={() => remove.mutate(item.cadence_id)}>Delete</button></div>
        </div>)}</div>
      </Panel>

      <Panel title="Create standing duty">
        <form className="stack" onSubmit={submit}>
          <label>Name<input value={name} onChange={e => setName(e.target.value)} placeholder="Monday engineering brief" /></label>
          <label>Objective<input value={objective} onChange={e => setObjective(e.target.value)} placeholder="Prepare the weekly engineering brief" /></label>
          <div className="cadence-schedule-grid">
            <label>Repeat<select value={kind} onChange={e => setKind(e.target.value as typeof kind)}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="interval">Interval</option></select></label>
            {kind === 'weekly' ? <label>Day<select value={weekday} onChange={e => setWeekday(Number(e.target.value))}>{WEEKDAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label> : null}
            {kind === 'interval' ? <label>Every (minutes)<input type="number" min={1} value={interval} onChange={e => setInterval(Number(e.target.value))} /></label> : <><label>Hour<input type="number" min={0} max={23} value={hour} onChange={e => setHour(Number(e.target.value))} /></label><label>Minute<input type="number" min={0} max={59} value={minute} onChange={e => setMinute(Number(e.target.value))} /></label></>}
            <label>Timezone<input value={timezone} onChange={e => setTimezone(e.target.value)} /></label>
          </div>
          <div className="cadence-step-head"><div><span className="eyebrow">Work steps</span><div className="meta">Deterministic capabilities Atlas will run in order.</div></div><button type="button" onClick={() => setSteps(current => [...current, { capability_id: available[0]?.id ?? '', input: '{}' }])}>Add step</button></div>
          <div className="stack">{steps.map((step, index) => <div className="cadence-step" key={index}>
            <div className="row-head"><strong>Step {index + 1}</strong>{steps.length > 1 ? <button className="danger" type="button" onClick={() => setSteps(current => current.filter((_, i) => i !== index))}>Remove</button> : null}</div>
            <label>Capability<select value={step.capability_id} onChange={e => setSteps(current => current.map((item, i) => i === index ? { ...item, capability_id: e.target.value } : item))}><option value={step.capability_id}>{step.capability_id || 'Select capability'}</option>{available.filter(item => item.id !== step.capability_id).map(item => <option key={item.id} value={item.id}>{item.id} — {item.description}</option>)}</select></label>
            <details className="inspect"><summary>Input payload</summary><textarea className="mono" value={step.input} onChange={e => setSteps(current => current.map((item, i) => i === index ? { ...item, input: e.target.value } : item))} /></details>
          </div>)}</div>
          {formError ? <p className="offline-banner">{formError}</p> : null}{create.isError ? <p className="offline-banner">{create.error.message}</p> : null}
          <button className="primary" type="submit" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create standing duty'}</button>
        </form>
      </Panel>
    </div>
  </Workspace>
}
