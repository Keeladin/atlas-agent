import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkItem } from '../api/types'
import { Panel } from '../ui/Panel'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace } from '../ui/Workspace'
import { OPERATIONS_TABS } from './operationsNav'

function stateClass(status: string) {
  if (status === 'completed') return 'done'
  if (status === 'active') return 'running'
  if (status === 'waiting_confirmation') return 'confirm'
  if (status === 'failed' || status === 'cancelled') return 'failed'
  return ''
}

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

  const rows = query.data?.work ?? []
  const open = rows.filter(item => !['completed', 'cancelled', 'failed'].includes(item.status)).length
  return <Workspace title="Work" subtitle="Durable responsibilities created by the runtime and carried to verified completion." tabs={<SegmentedNav items={OPERATIONS_TABS} />}>
    <div className="work-summary-strip">
      <div><span>Open</span><strong>{open}</strong></div>
      <div><span>Waiting on you</span><strong>{rows.filter(item => item.status === 'waiting_confirmation').length}</strong></div>
      <div><span>Completed</span><strong>{rows.filter(item => item.status === 'completed').length}</strong></div>
      <div><span>Total</span><strong>{rows.length}</strong></div>
    </div>

    <Panel title="Responsibilities">
      {query.isError ? <p className="offline-banner">{query.error.message}</p> : null}
      <div className="responsibility-list">{rows.map(item => <Link className="responsibility-card" key={item.work_id} to={`/work/${item.work_id}`}>
        <div className="responsibility-ref"><span className="eyebrow">{item.display_ref ?? 'Work'}</span><small>{String(item.metadata?.workflow_intent ?? item.workflow_class ?? 'runtime')}</small></div>
        <div className="responsibility-objective"><strong>{item.objective}</strong><small>{item.metadata?.source_artifact_id ? `Source ${String(item.metadata.source_artifact_id)}` : 'Owner-created responsibility'}</small></div>
        <span className={`chip ${stateClass(item.status)}`}>{item.status.replaceAll('_', ' ')}</span>
      </Link>)}</div>
      {!rows.length && !query.isLoading ? <div className="empty-state"><strong>No durable Work yet</strong><span>Responsibilities created by intake, chat, or cadence will appear here.</span></div> : null}
    </Panel>
    <details className="inspect engineering-create">
      <summary>Engineering · create Work manually</summary>
      <form onSubmit={submit} className="stack">
        <label>Objective<input value={objective} onChange={e => setObjective(e.target.value)} placeholder="What Atlas owns until done" /></label>
        <label>Capability steps<textarea className="mono" value={steps} onChange={e => setSteps(e.target.value)} spellCheck={false} /></label>
        <button className="primary" type="submit" disabled={!objective.trim() || create.isPending}>Create and run</button>
        {create.isError ? <p className="offline-banner">{create.error.message}</p> : null}
      </form>
    </details>
  </Workspace>
}
