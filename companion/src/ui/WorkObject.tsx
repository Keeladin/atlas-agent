import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkItem, WorkStep } from '../api/types'
import { StatusLamp } from './OperationsPrimitives'
import { workStateToLamp } from './operationState'

type WorkDetail = WorkItem & {
  steps: WorkStep[]
  revision?: number
  adaptations?: Array<{
    adaptation_id: string
    new_revision: number
    change_intent: string
    reason: string
    unchanged_goal: string
    expected_impact: string
    created_at: string
  }>
}

function stepTone(status: string) {
  if (status === 'completed') return 'done'
  if (status === 'running') return 'active'
  if (status === 'failed') return 'failed'
  if (status === 'waiting') return 'waiting'
  return 'queued'
}

export function WorkObject({ workId, initial }: { workId: string; initial?: Partial<WorkDetail> }) {
  const qc = useQueryClient()
  const detail = useQuery({
    queryKey: ['work-detail', workId],
    queryFn: () => api<WorkDetail>(`/api/work/${workId}`),
    refetchInterval: query => ['active', 'queued', 'waiting', 'paused'].includes(String(query.state.data?.status || '')) ? 2500 : false,
    initialData: initial?.steps ? initial as WorkDetail : undefined,
  })
  const action = useMutation({
    mutationFn: (name: 'resume' | 'pause' | 'cancel') => api<WorkDetail>(`/api/work/${workId}/${name}`, { method: 'POST', body: '{}' }),
    onSuccess: async data => {
      qc.setQueryData(['work-detail', workId], data)
      await qc.invalidateQueries({ queryKey: ['work'] })
    },
  })
  const work = detail.data
  if (!work) return <section className="runtime-object work-object"><div className="runtime-object-head"><span className="runtime-object-copy"><strong>Work</strong><small>{detail.isError ? detail.error.message : 'Loading runtime truth…'}</small></span></div></section>
  const latestAdaptation = work.adaptations?.at(-1)
  const canResume = ['paused', 'failed', 'waiting'].includes(work.status)
  const canPause = ['active', 'queued'].includes(work.status)
  const canCancel = !['completed', 'cancelled'].includes(work.status)
  return <section className="runtime-object work-object">
    <header className="work-object-head">
      <span className="work-object-state"><StatusLamp tone={workStateToLamp(work.status)} /></span>
      <span className="runtime-object-copy">
        <small>{work.display_ref ?? `Work · revision ${work.revision ?? 1}`}</small>
        <strong>{work.objective}</strong>
      </span>
      <span className="work-object-status">{work.status.replaceAll('_', ' ')}</span>
    </header>
    <ol className="work-object-plan">{(work.steps ?? []).map(step => <li key={step.step_id} className={stepTone(step.status)}>
      <span className="work-object-step-marker">{step.status === 'completed' ? '✓' : step.ordinal}</span>
      <span><strong>{step.description || step.capability_id}</strong><small>{step.capability_id}</small></span>
      <em>{step.status}</em>
    </li>)}</ol>
    {latestAdaptation ? <aside className="work-object-adaptation">
      <span>Route changed · revision {latestAdaptation.new_revision}</span>
      <strong>{latestAdaptation.change_intent}</strong>
      <p>{latestAdaptation.reason}</p>
      <dl><div><dt>Unchanged</dt><dd>{latestAdaptation.unchanged_goal}</dd></div><div><dt>Impact</dt><dd>{latestAdaptation.expected_impact}</dd></div></dl>
    </aside> : null}
    <footer className="runtime-object-footer">
      <Link to={`/work/${workId}`}>Open full Work</Link>
      <span className="runtime-object-controls">
        {canResume ? <button type="button" disabled={action.isPending} onClick={() => action.mutate('resume')}>Resume</button> : null}
        {canPause ? <button type="button" disabled={action.isPending} onClick={() => action.mutate('pause')}>Pause</button> : null}
        {canCancel ? <button type="button" disabled={action.isPending} onClick={() => action.mutate('cancel')}>Cancel</button> : null}
      </span>
    </footer>
    {action.isError ? <p className="runtime-object-error">{action.error.message}</p> : null}
  </section>
}
