import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkItem } from '../api/types'
import { Panel } from '../ui/Panel'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace } from '../ui/Workspace'

const WORK_TABS = [{ to: '/work', label: 'Work', end: true }, { to: '/cadence', label: 'Cadence' }]

export function WorkList() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work') })
  const [objective, setObjective] = useState('')
  const [steps, setSteps] = useState('[\n  {"capability_id":"knowledge.search","input":{"query":"example"}}\n]')
  const create = useMutation({
    mutationFn: (payload: object) => api('/api/work', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: async () => { setObjective(''); await qc.invalidateQueries({ queryKey: ['work'] }) },
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    try { create.mutate({ objective, steps: JSON.parse(steps), run: true }) } catch { /* rendered below */ }
  }
  return <Workspace title="Work" subtitle="Durable responsibility with current policy re-resolved at every side effect." tabs={<SegmentedNav items={WORK_TABS} />}>
    <div className="grid-2">
      <Panel title="Active and recent">
        <div className="stack">{(query.data?.work ?? []).map(item => <Link className="list-row" key={item.work_id} to={`/work/${item.work_id}`}><strong>{item.objective}</strong><span className="chip">{item.status}</span><div className="meta">{item.updated_at}</div></Link>)}</div>
        {!query.data?.work.length ? <p className="empty">No durable Work yet.</p> : null}
      </Panel>
      <Panel title="Create Work">
        <form onSubmit={submit} className="stack">
          <label>Objective<input value={objective} onChange={e => setObjective(e.target.value)} placeholder="What Atlas owns until done" /></label>
          <label>Capability steps<textarea value={steps} onChange={e => setSteps(e.target.value)} spellCheck={false} /></label>
          <button className="primary" type="submit" disabled={!objective.trim() || create.isPending}>Create and run</button>
          {create.isError ? <p className="offline-banner">{create.error.message}</p> : null}
        </form>
      </Panel>
    </div>
  </Workspace>
}
