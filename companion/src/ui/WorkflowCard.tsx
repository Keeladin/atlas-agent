import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionOccurrence, Cadence, Capability, WorkItem } from '../api/types'
import { SchemaForm } from './SchemaForm'
import { StatusLamp } from './OperationsPrimitives'
import { readSteps, scheduleLabel, stepLabel, when, workflowVariant, type WorkflowStep, type WorkflowVariant } from './workflowPresentation'

const EYEBROW: Record<WorkflowVariant, string> = {
  'cadence-created': 'Standing duty created',
  'cadence-updated': 'Standing duty updated',
  'cadence-run': 'Standing duty run',
  'work-created': 'Work created',
}

function StepList({ steps, capabilities, onEdit, editing }: {
  steps: WorkflowStep[]
  capabilities?: Capability[]
  onEdit?: (index: number, input: Record<string, unknown>) => void
  editing?: boolean
}) {
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  if (!steps.length) return <p className="meta">No steps recorded.</p>
  return <ol className="workflow-card-steps">{steps.map((step, index) => {
    const capability = capabilities?.find(item => item.id === step.capability_id)
    const open = openIndex === index
    return <li key={`${step.capability_id}:${index}`}>
      <div className="workflow-card-step">
        <span className="workflow-card-step-index">{index + 1}</span>
        <span className="workflow-card-step-body">
          <strong>{stepLabel(step)}</strong>
          {stepLabel(step) === step.capability_id ? null : <small className="mono">{step.capability_id}</small>}
        </span>
        {onEdit && capability ? <button type="button" className="workflow-card-step-edit" onClick={() => { setOpenIndex(open ? null : index); setDraft({ ...(step.input ?? {}) }) }}>{open ? 'Close' : 'Edit input'}</button> : null}
      </div>
      {open && capability && onEdit ? <div className="workflow-card-step-form">
        <SchemaForm schema={capability.input_schema} value={draft} onChange={setDraft} />
        <button type="button" className="primary" disabled={editing} onClick={() => { onEdit(index, draft); setOpenIndex(null) }}>{editing ? 'Saving…' : 'Save step input'}</button>
      </div> : null}
    </li>
  })}</ol>
}

export function WorkflowCard({ action }: { action: ActionOccurrence }) {
  const qc = useQueryClient()
  const variant = workflowVariant(action.capability_id)
  const [message, setMessage] = useState<string | null>(null)
  const capabilities = useQuery({
    queryKey: ['capabilities'],
    queryFn: () => api<{ capabilities: Capability[] }>('/api/capabilities'),
    enabled: variant === 'cadence-created' || variant === 'cadence-updated',
  })
  const invoke = useMutation({
    mutationFn: ({ capability, input }: { capability: string; input: Record<string, unknown> }) =>
      api<{ action: ActionOccurrence }>(`/api/capabilities/${capability}/invoke`, { method: 'POST', body: JSON.stringify({ input }) }),
    onSuccess: async ({ action: result }) => {
      setMessage(result.status === 'succeeded' ? null : result.error || `Runtime returned ${result.status}.`)
      await Promise.all([qc.invalidateQueries({ queryKey: ['cadence'] }), qc.invalidateQueries({ queryKey: ['work'] })])
    },
    onError: error => setMessage(error instanceof Error ? error.message : String(error)),
  })
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const toggle = useMutation({
    mutationFn: ({ cadenceId, next }: { cadenceId: string; next: boolean }) =>
      api<Cadence>(`/api/cadence/${cadenceId}/enabled`, { method: 'POST', body: JSON.stringify({ enabled: next }) }),
    onSuccess: async item => { setEnabled(item.enabled); await qc.invalidateQueries({ queryKey: ['cadence'] }) },
    onError: error => setMessage(error instanceof Error ? error.message : String(error)),
  })

  if (!variant || action.status !== 'succeeded') return null
  const result = (action.result ?? {}) as Record<string, unknown>

  if (variant === 'cadence-run') {
    const workId = typeof result.work_id === 'string' ? result.work_id : null
    return <section className="workflow-card" aria-label={EYEBROW[variant]}>
      <header><span className="eyebrow">{EYEBROW[variant]}</span><strong>Ran now, schedule unchanged</strong></header>
      {workId
        ? <Link className="workflow-card-link" to={`/work/${workId}`}><StatusLamp tone="blue" /><span>Open the Work this run created</span></Link>
        : <p className="meta">{String(result.kind ?? 'run')} completed without creating Work.</p>}
    </section>
  }

  if (variant === 'work-created') {
    const work = result as unknown as WorkItem
    const steps = readSteps((result.steps as unknown) ?? [])
    return <section className="workflow-card" aria-label={EYEBROW[variant]}>
      <header><span className="eyebrow">{EYEBROW[variant]}</span><strong>{work.objective || 'Work'}</strong></header>
      <StepList steps={steps} />
      {work.work_id ? <Link className="workflow-card-link" to={`/work/${work.work_id}`}><StatusLamp tone="blue" /><span>Open full Work</span></Link> : null}
    </section>
  }

  const cadence = result as unknown as Cadence
  const steps = readSteps(cadence.steps)
  function saveStep(index: number, input: Record<string, unknown>) {
    const next = steps.map((step, position) => position === index
      ? { capability_id: step.capability_id, ...(step.description ? { description: step.description } : {}), input }
      : { capability_id: step.capability_id, ...(step.description ? { description: step.description } : {}), input: step.input ?? {} })
    invoke.mutate({ capability: 'cadence.update', input: { cadence_id: cadence.cadence_id, steps: next } })
  }
  return <section className="workflow-card" aria-label={EYEBROW[variant]}>
    <header>
      <span className="eyebrow">{EYEBROW[variant]}</span>
      <strong>{cadence.name}</strong>
      <small>{scheduleLabel(cadence.schedule ?? {})} · next {when(cadence.next_run_at)}</small>
    </header>
    <StepList steps={steps} capabilities={capabilities.data?.capabilities} onEdit={saveStep} editing={invoke.isPending} />
    <div className="workflow-card-actions">
      <button type="button" disabled={invoke.isPending} onClick={() => invoke.mutate({ capability: 'cadence.run_now', input: { cadence_id: cadence.cadence_id } })}>Run now</button>
      <button type="button" disabled={toggle.isPending} onClick={() => toggle.mutate({ cadenceId: cadence.cadence_id, next: !(enabled ?? cadence.enabled) })}>{(enabled ?? cadence.enabled) ? 'Disable' : 'Enable'}</button>
      <Link className="button-link" to="/cadence">Open Cadence</Link>
    </div>
    {message ? <p className="offline-banner">{message}</p> : null}
  </section>
}
