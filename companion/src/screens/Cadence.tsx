import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { Cadence } from '../api/types'
import { Panel } from '../ui/Panel'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace } from '../ui/Workspace'

const TABS = [{ to: '/work', label: 'Work', end: true }, { to: '/cadence', label: 'Cadence', end: true }]

export function CadenceList() {
  const qc = useQueryClient(); const query = useQuery({ queryKey: ['cadence'], queryFn: () => api<{ cadences: Cadence[] }>('/api/cadence') })
  const [name, setName] = useState(''); const [objective, setObjective] = useState('')
  const [schedule, setSchedule] = useState('{"kind":"daily","hour":8,"minute":0,"timezone":"Africa/Johannesburg"}')
  const [steps, setSteps] = useState('[{"capability_id":"knowledge.search","input":{"query":"daily brief"}}]')
  const create = useMutation({ mutationFn: (payload: object) => api('/api/cadence', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: () => qc.invalidateQueries({ queryKey: ['cadence'] }) })
  const enable = useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api(`/api/cadence/${id}/enabled`, { method: 'POST', body: JSON.stringify({ enabled }) }), onSuccess: () => qc.invalidateQueries({ queryKey: ['cadence'] }) })
  function submit(event: FormEvent) { event.preventDefault(); try { create.mutate({ name, objective, schedule: JSON.parse(schedule), steps: JSON.parse(steps) }) } catch { /* invalid JSON remains visible */ } }
  return <Workspace title="Cadence" subtitle="Recurring standing duties that instantiate ordinary Work." tabs={<SegmentedNav items={TABS} />}>
    <div className="grid-2">
      <Panel title="Standing duties"><div className="stack">{(query.data?.cadences ?? []).map(item => <div className="list-row" key={item.cadence_id}><strong>{item.name}</strong><span className="chip">{item.enabled ? 'enabled' : 'disabled'}</span><div>{item.objective}</div><div className="meta">Next: {item.next_run_at || 'not scheduled'}</div><button onClick={() => enable.mutate({ id: item.cadence_id, enabled: !item.enabled })}>{item.enabled ? 'Disable' : 'Enable'}</button></div>)}</div></Panel>
      <Panel title="Create Cadence"><form className="stack" onSubmit={submit}><label>Name<input value={name} onChange={e => setName(e.target.value)} /></label><label>Objective<input value={objective} onChange={e => setObjective(e.target.value)} /></label><label>Schedule JSON<textarea value={schedule} onChange={e => setSchedule(e.target.value)} /></label><label>Steps JSON<textarea value={steps} onChange={e => setSteps(e.target.value)} /></label><button className="primary" type="submit">Create</button></form></Panel>
    </div>
  </Workspace>
}
