import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { PendingApproval, PendingConfirmation, WorkDetail as WorkDetailType, WorkExecution } from '../api/types'
import { humanCapabilityLabel, isExecutableRun, phaseChip, runActionLabel } from '../lib/workLabels'
import { Chip } from '../ui/Chip'
import { Inspect } from '../ui/Inspect'
import { Panel } from '../ui/Panel'
import { StepProgress } from '../ui/StepProgress'
import { LifecycleControls, Workspace } from '../ui/Workspace'

export function WorkDetail() {
  const { workId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const detailQuery = useQuery({
    queryKey: ['work-detail', workId],
    queryFn: () => api<WorkDetailType>(`/api/work/${workId}/detail`),
    enabled: Boolean(workId),
    refetchInterval: (query) => ['running', 'active'].includes(query.state.data?.phase || '') ? 2500 : false,
  })
  useWorkEventStream(workId)

  const refreshDetail = () => queryClient.invalidateQueries({ queryKey: ['work-detail', workId] })
  const runMutation = useWorkAction(workId, 'run', refreshDetail)
  const recoverMutation = useWorkAction(workId, 'recover', refreshDetail)
  const cancelMutation = useWorkAction(workId, 'cancel', refreshDetail)
  const pauseMutation = useWorkAction(workId, 'pause', refreshDetail)
  const archiveMutation = useMutation({
    mutationFn: (archived: boolean) => api(`/api/work/${workId}/archive`, { method: 'POST', body: JSON.stringify({ archived }) }),
    onSuccess: () => { void refreshDetail(); void queryClient.invalidateQueries({ queryKey: ['work'] }) },
  })
  const deleteMutation = useMutation({
    mutationFn: () => api(`/api/work/${workId}`, { method: 'DELETE' }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['work'] }); navigate('/work') },
  })

  const detail = detailQuery.data
  if (detailQuery.isLoading) return <p className="empty">Loading work…</p>
  if (detailQuery.isError) return <div className="offline-banner">{detailQuery.error instanceof Error ? detailQuery.error.message : 'Could not load this work.'}</div>
  if (!detail) return null

  const chip = phaseChip(detail)
  const blockingConfirmation = detail.phase === 'waiting_confirmation'
  const blockingAuthority = detail.phase === 'waiting_authority'
  const runningExecution = detail.executions.find((item) => item.status === 'running')
  const runningStep = detail.steps.find((item) => item.status === 'running')
  const currentStep = runningExecution ? detail.steps.find((step) => step.id === runningExecution.step_id) ?? null : runningStep ?? null
  const activeCapability = runningExecution?.capability || runningStep?.capability || null
  const blockedStep = detail.steps.find((item) => item.status === 'blocked')

  return <Workspace title={detail.objective} crumb={<><Link to="/work">Work</Link> / Current</>} subtitle={detail.blocking?.message || stateSentence(detail)} headerActions={<Chip tone={chip.tone}>{chip.label}</Chip>}>
    <div className="work-operator-stack">
      <Panel tone={attentionTone(detail)} className="work-state-panel">
        <div className="work-state-head"><div><div className="eyebrow">Current state</div><h2>{stateTitle(detail)}</h2></div><Chip tone={chip.tone}>{chip.label}</Chip></div>
        <p className="work-state-message">{detail.blocking?.message || stateSentence(detail)}</p>
        {activeCapability ? <div className="work-current-fact"><span>Active capability</span><strong>{humanCapabilityLabel(activeCapability)}</strong>{currentStep ? <small>Step {currentStep.ordinal}: {currentStep.description}</small> : null}</div> : currentStep ? <div className="work-current-fact"><span>Current step</span><strong>Step {currentStep.ordinal}: {currentStep.description}</strong></div> : blockedStep ? <div className="work-current-fact"><span>Blocked step</span><strong>Step {blockedStep.ordinal}: {blockedStep.description}</strong></div> : null}
      </Panel>

      {blockingAuthority ? detail.pending_approvals.map((item) => <AuthorityCard key={item.id} item={item} onDone={refreshDetail} />) : null}
      {blockingConfirmation ? detail.pending_confirmations.map((item) => <ConfirmationCard key={item.id} item={item} onDone={refreshDetail} />) : null}

      <div className="work-runtime-grid">
        <Panel title="Authority"><div className="work-fact-list"><div><span>Granted scope</span><strong>{detail.authority_scope}</strong></div>{blockingAuthority ? <div><span>Needs authority</span><strong>Atlas cannot continue until you decide.</strong></div> : null}{blockingConfirmation ? <div><span>Payload confirmation</span><strong>Authority is separate; this is approval of an exact action.</strong></div> : null}{!blockingAuthority && !blockingConfirmation ? <p className="empty compact">No authority decision is blocking this Work.</p> : null}</div></Panel>
        <Panel title="Execution"><div className="work-fact-list"><div><span>Runtime phase</span><strong>{chip.label}</strong></div>{runningExecution ? <ExecutionSummary execution={runningExecution} /> : null}{!runningExecution && runningStep?.capability ? <div><span>Running step capability</span><strong>{humanCapabilityLabel(runningStep.capability)}</strong></div> : null}{!runningExecution && !runningStep && detail.phase === 'pausing' ? <div><span>Execution</span><strong>Stopping at the next safe point.</strong></div> : null}{!runningExecution && !runningStep && detail.phase === 'paused' ? <div><span>Execution</span><strong>Paused; nothing else will start.</strong></div> : null}</div><div className="work-step-progress"><StepProgress steps={detail.steps} /></div></Panel>
        <Panel title="Inputs / evidence"><EvidenceSummary detail={detail} /></Panel>
      </div>

      <Panel title="Controls"><p className="meta" style={{ marginTop: 0 }}>Controls appear only when this WorkRuntime currently allows them.</p><LifecycleControls>
        {isExecutableRun(detail) && runActionLabel(detail) ? <button className="primary" type="button" disabled={runMutation.isPending} onClick={() => runMutation.mutate()}>{runActionLabel(detail)}</button> : null}
        {detail.actions.includes('pause') ? <button className="warn" type="button" disabled={pauseMutation.isPending} onClick={() => pauseMutation.mutate()}>Pause</button> : null}
        {detail.actions.includes('recover') ? <button className="warn" type="button" disabled={recoverMutation.isPending} onClick={() => recoverMutation.mutate()}>Retry / Recover</button> : null}
        {detail.actions.includes('cancel') ? <button className="danger" type="button" disabled={cancelMutation.isPending} onClick={() => cancelMutation.mutate()}>Cancel</button> : null}
        {detail.actions.includes('archive') ? <button type="button" disabled={archiveMutation.isPending} onClick={() => archiveMutation.mutate(true)}>Archive</button> : null}
        {detail.actions.includes('unarchive') ? <button type="button" disabled={archiveMutation.isPending} onClick={() => archiveMutation.mutate(false)}>Restore</button> : null}
        {detail.actions.includes('delete') && !confirmDelete ? <button className="danger" type="button" onClick={() => setConfirmDelete(true)}>Delete</button> : null}
        {detail.actions.includes('delete') && confirmDelete ? <><button className="danger" type="button" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>Delete permanently</button><button type="button" onClick={() => setConfirmDelete(false)}>Keep</button></> : null}
        {!detail.actions.some((action) => ['run', 'pause', 'recover', 'cancel', 'archive', 'unarchive', 'delete'].includes(action)) ? <p className="empty compact">No lifecycle actions are available in this state.</p> : null}
      </LifecycleControls></Panel>

      <Panel title="Deliverable / outcome" tone={detail.status === 'failed' ? 'failed' : undefined}><Outcome detail={detail} /></Panel>
      <Panel title="Runtime activity"><div className="work-activity" aria-label="Runtime activity log">{[...detail.events].reverse().map((event) => <div key={event.id} className="work-activity-row"><time>{event.created_at}</time><span>{humanEventName(event.name)}</span></div>)}{!detail.events.length ? <p className="empty compact">No runtime activity yet.</p> : null}</div><Inspect label="Inspect technical details">{JSON.stringify({ work_id: detail.work_id, phase: detail.phase, status: detail.status, contract: detail.contract, capabilities: detail.capabilities, events: detail.events }, null, 2)}</Inspect></Panel>
    </div>
  </Workspace>
}

function useWorkAction(workId: string, action: 'run' | 'recover' | 'cancel' | 'pause', onDone: () => Promise<unknown>) {
  return useMutation({ mutationFn: () => api(`/api/work/${workId}/${action}`, { method: 'POST', body: '{}' }), onSuccess: () => onDone() })
}

function useWorkEventStream(workId: string) {
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!workId) return
    let cursor = '0'; let source: EventSource | null = null; let reconnectTimer: number | null = null; let stopped = false
    const refresh = (event: Event) => { if (event instanceof MessageEvent && event.lastEventId) cursor = event.lastEventId; void queryClient.invalidateQueries({ queryKey: ['work-detail', workId] }); void queryClient.invalidateQueries({ queryKey: ['work'] }) }
    const connect = () => { source = new EventSource(`/api/work/${workId}/events/stream?after=${cursor}`, { withCredentials: true }); for (const name of ['confirmation.requested', 'confirmation.applied', 'approval.requested', 'approval.applied', 'work.paused', 'work.pause_requested', 'work.resumed', 'work.completed', 'work.failed', 'capability.completed']) source.addEventListener(name, refresh); source.onmessage = refresh; source.onerror = () => { source?.close(); if (!stopped) reconnectTimer = window.setTimeout(connect, 1000) } }
    connect()
    return () => { stopped = true; source?.close(); if (reconnectTimer !== null) window.clearTimeout(reconnectTimer) }
  }, [queryClient, workId])
}

function AuthorityCard({ item, onDone }: { item: PendingApproval; onDone: () => Promise<unknown> }) {
  const approve = useMutation({ mutationFn: () => api(`/api/work/approvals/${item.id}/approve`, { method: 'POST', body: JSON.stringify({ note: 'approved in Companion' }) }), onSuccess: onDone })
  const deny = useMutation({ mutationFn: () => api(`/api/work/approvals/${item.id}/deny`, { method: 'POST', body: JSON.stringify({ note: 'denied in Companion' }) }), onSuccess: onDone })
  return <Panel title="Authority decision required" tone="decision-auth" className="work-decision"><p style={{ marginTop: 0 }}>Atlas needs <strong>{item.required_authority}</strong> authority before continuing.</p><p className="meta">{item.requested_action}</p><div className="actions"><button className="authority" type="button" disabled={approve.isPending} onClick={() => approve.mutate()}>Approve authority</button><button className="danger" type="button" disabled={deny.isPending} onClick={() => deny.mutate()}>Deny authority</button></div></Panel>
}

function ConfirmationCard({ item, onDone }: { item: PendingConfirmation; onDone: () => Promise<unknown> }) {
  const confirm = useMutation({ mutationFn: () => api(`/api/work/confirmations/${item.id}/confirm`, { method: 'POST', body: '{}' }), onSuccess: onDone })
  const deny = useMutation({ mutationFn: () => api(`/api/work/confirmations/${item.id}/deny`, { method: 'POST', body: '{}' }), onSuccess: onDone })
  const cancel = useMutation({ mutationFn: () => api(`/api/work/confirmations/${item.id}/cancel`, { method: 'POST', body: '{}' }), onSuccess: onDone })
  const input = item.payload.invocation_input
  const invocation = typeof input === 'object' && input ? input as Record<string, unknown> : null
  return <Panel title="Payload confirmation required" tone="decision-confirm" className="work-decision"><p style={{ marginTop: 0, fontSize: '1.05rem', lineHeight: 1.5 }}>{item.summary}</p><p className="meta">Confirm this exact payload; it does not grant a new authority scope.</p>{invocation ? <div className="work-payload-preview">{Object.entries(invocation).map(([key, value]) => <div key={key} className="brief-row"><span>{key}</span><div>{typeof value === 'string' ? value : JSON.stringify(value)}</div></div>)}</div> : null}<div className="actions"><button className="confirm" type="button" disabled={confirm.isPending} onClick={() => confirm.mutate()}>Confirm payload</button><button className="danger" type="button" disabled={deny.isPending} onClick={() => deny.mutate()}>Deny confirmation</button><button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel confirmation</button></div><Inspect label="Inspect exact payload">{JSON.stringify(item.payload, null, 2)}</Inspect></Panel>
}

function ExecutionSummary({ execution }: { execution: WorkExecution }) { return <div><span>Active execution</span><strong>{humanCapabilityLabel(execution.capability)}</strong><small>Attempt {execution.attempt}{execution.started_at ? ` · started ${execution.started_at}` : ''}</small></div> }

function EvidenceSummary({ detail }: { detail: WorkDetailType }) {
  const failedExecution = detail.executions.find((item) => item.status === 'failed')
  return <div className="work-fact-list"><div><span>Artifacts</span><strong>{detail.artifacts.length ? `${detail.artifacts.length} available` : 'None yet'}</strong></div><div><span>Claims</span><strong>{detail.claims.length ? `${detail.claims.length} recorded` : 'None yet'}</strong></div><div><span>Execution attempts</span><strong>{detail.executions.length ? `${detail.executions.length} recorded` : 'None yet'}</strong></div>{failedExecution ? <div><span>Failed attempt</span><strong>{humanCapabilityLabel(failedExecution.capability)}{failedExecution.error ? ` — ${failedExecution.error}` : ''}</strong></div> : null}</div>
}

function Outcome({ detail }: { detail: WorkDetailType }) {
  const completed = detail.status === 'completed'; const failed = detail.status === 'failed'
  return <div className="work-outcome"><p className={failed ? 'error-text' : ''} style={{ marginTop: 0 }}>{completed ? "Done — here's what changed." : failed ? "I couldn't finish this. Review the failed execution and runtime activity." : detail.phase === 'unavailable' ? detail.blocking?.message || "I can't do this yet." : 'Outcome appears as Atlas completes this responsibility.'}</p>{detail.artifacts.length ? <div className="work-artifact-list">{detail.artifacts.map((artifact) => <details key={artifact.id} className="work-artifact"><summary><strong>{artifact.kind}</strong><span>{artifact.created_at}</span></summary><Inspect label="Inspect artifact">{JSON.stringify(artifact.payload, null, 2)}</Inspect></details>)}</div> : <p className="empty compact">No artifacts yet.</p>}{detail.claims.length ? <div className="work-claims"><h3>Claims</h3>{detail.claims.map((claim) => <div key={claim.id} className="work-claim"><strong>{claim.subject}</strong><span>{claim.kind}</span></div>)}</div> : <p className="empty compact">No evidence claims yet.</p>}</div>
}

function attentionTone(detail: WorkDetailType) { if (detail.status === 'failed' || detail.phase === 'unavailable') return 'failed'; if (detail.phase === 'waiting_confirmation') return 'decision-confirm'; if (detail.phase === 'waiting_authority') return 'decision-auth'; return undefined }
function stateTitle(detail: WorkDetailType) { if (detail.phase === 'waiting_confirmation') return 'Waiting for payload confirmation'; if (detail.phase === 'waiting_authority') return 'Waiting for authority approval'; if (detail.phase === 'pausing') return 'Pausing at a safe point'; if (detail.phase === 'paused') return 'Work is paused'; if (detail.phase === 'running') return 'Atlas is executing this Work'; if (detail.status === 'completed') return 'Work completed'; if (detail.status === 'failed') return 'Work failed'; return phaseChip(detail).label }
function stateSentence(detail: WorkDetailType) { if (detail.status === 'completed') return 'Atlas completed this responsibility.'; if (detail.status === 'failed') return 'Atlas could not complete this responsibility.'; if (detail.phase === 'paused') return 'Nothing else will start until you resume.'; return 'Atlas is carrying this responsibility.' }
function humanEventName(name: string) { const map: Record<string, string> = { 'work.started': 'Work started', 'work.paused': 'Paused at a safe point', 'work.pause_requested': 'Pause requested', 'work.resumed': 'Work resumed', 'work.completed': 'Work completed', 'work.failed': 'Work failed', 'work.archived': 'Archived', 'work.unarchived': 'Restored from archive', 'confirmation.requested': 'Payload confirmation requested', 'confirmation.applied': 'Payload confirmation received', 'approval.requested': 'Authority approval requested', 'approval.applied': 'Authority approval received', 'capability.completed': 'Capability completed', 'capability.started': 'Capability started' }; return map[name] || name.replace(/\./g, ' ') }
