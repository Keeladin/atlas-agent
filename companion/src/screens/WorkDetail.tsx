import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkDetail as WorkDetailType } from '../api/types'
import {
  humanCapabilityLabel,
  isExecutableRun,
  phaseChip,
  runActionLabel,
  stepTone,
} from '../lib/workLabels'
import { Chip } from '../ui/Chip'
import { Inspect } from '../ui/Inspect'
import { Panel } from '../ui/Panel'
import { StepProgress } from '../ui/StepProgress'
import {
  LifecycleControls,
  Workspace,
  WorkspaceRailSection,
} from '../ui/Workspace'

export function WorkDetail() {
  const { workId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const detailQuery = useQuery({
    queryKey: ['work-detail', workId],
    queryFn: () => api<WorkDetailType>(`/api/work/${workId}/detail`),
    enabled: Boolean(workId),
    refetchInterval: (query) => {
      const phase = query.state.data?.phase
      return phase === 'running' || phase === 'active' ? 2500 : false
    },
  })

  useEffect(() => {
    if (!workId) return
    let cursor = '0'
    let source: EventSource | null = null
    let reconnectTimer: number | null = null
    let stopped = false
    const refresh = (event: Event) => {
      if (event instanceof MessageEvent && event.lastEventId) {
        cursor = event.lastEventId
      }
      void queryClient.invalidateQueries({ queryKey: ['work-detail', workId] })
      void queryClient.invalidateQueries({ queryKey: ['work'] })
    }
    const connect = () => {
      source = new EventSource(
        `/api/work/${workId}/events/stream?after=${cursor}`,
        { withCredentials: true },
      )
      source.addEventListener('confirmation.requested', refresh)
      source.addEventListener('confirmation.applied', refresh)
      source.addEventListener('approval.requested', refresh)
      source.addEventListener('approval.applied', refresh)
      source.addEventListener('work.paused', refresh)
      source.addEventListener('work.pause_requested', refresh)
      source.addEventListener('work.resumed', refresh)
      source.addEventListener('work.completed', refresh)
      source.addEventListener('work.failed', refresh)
      source.addEventListener('capability.completed', refresh)
      source.onmessage = refresh
      source.onerror = () => {
        source?.close()
        if (!stopped) reconnectTimer = window.setTimeout(connect, 1000)
      }
    }
    connect()
    return () => {
      stopped = true
      source?.close()
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
    }
  }, [workId, queryClient])

  const runMutation = useMutation({
    mutationFn: () =>
      api(`/api/work/${workId}/run`, { method: 'POST', body: '{}' }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['work-detail', workId] }),
  })
  const recoverMutation = useMutation({
    mutationFn: () =>
      api(`/api/work/${workId}/recover`, { method: 'POST', body: '{}' }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['work-detail', workId] }),
  })
  const cancelMutation = useMutation({
    mutationFn: () =>
      api(`/api/work/${workId}/cancel`, { method: 'POST', body: '{}' }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['work-detail', workId] }),
  })
  const pauseMutation = useMutation({
    mutationFn: () =>
      api(`/api/work/${workId}/pause`, { method: 'POST', body: '{}' }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['work-detail', workId] }),
  })
  const archiveMutation = useMutation({
    mutationFn: (archived: boolean) =>
      api(`/api/work/${workId}/archive`, {
        method: 'POST',
        body: JSON.stringify({ archived }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['work-detail', workId] })
      void queryClient.invalidateQueries({ queryKey: ['work'] })
    },
  })
  const deleteMutation = useMutation({
    mutationFn: () =>
      api(`/api/work/${workId}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['work'] })
      navigate('/work')
    },
  })

  const detail = detailQuery.data
  if (detailQuery.isLoading) return <p className="empty">Loading work…</p>
  if (detailQuery.isError) {
    return (
      <div className="offline-banner">
        {(detailQuery.error as Error).message || 'Could not load this work.'}
      </div>
    )
  }
  if (!detail) return null

  const chip = phaseChip(detail)
  const waitingNotRunning =
    detail.phase === 'waiting_confirmation' ||
    detail.phase === 'waiting_authority'
  const runLabel = runActionLabel(detail)
  const canRun = isExecutableRun(detail)
  const canPause = detail.actions.includes('pause')
  const canRecover = detail.actions.includes('recover')
  const canCancel = detail.actions.includes('cancel')
  const canArchive = detail.actions.includes('archive')
  const canUnarchive = detail.actions.includes('unarchive')
  const canDelete = detail.actions.includes('delete')
  const irreversibleDone = detail.steps.some(
    (step) => step.status === 'pass' || step.status === 'skipped',
  )

  const rail = (
    <Panel>
      <WorkspaceRailSection title="Work">
        <Link to="/work" className="list-row">
          <div>
            <strong>All work</strong>
            <div className="meta">Back to the list</div>
          </div>
        </Link>
        <Link to="/work/new" className="list-row">
          <div>
            <strong>Plan new work</strong>
            <div className="meta">Start another responsibility</div>
          </div>
        </Link>
      </WorkspaceRailSection>
      <WorkspaceRailSection title="You are in charge">
        <p className="meta" style={{ marginTop: 0 }}>
          Atlas does the work. You stay in charge. Controls appear only when
          they are real for this host and this moment.
        </p>
        <LifecycleControls>
          {canRun && runLabel ? (
            <button
              className="primary"
              type="button"
              disabled={runMutation.isPending}
              onClick={() => runMutation.mutate()}
            >
              {runLabel}
            </button>
          ) : null}
          {canPause ? (
            <button
              className="warn"
              type="button"
              disabled={pauseMutation.isPending}
              onClick={() => pauseMutation.mutate()}
            >
              Pause
            </button>
          ) : null}
          {detail.phase === 'pausing' ? (
            <p className="meta" style={{ margin: 0 }}>
              Stopping at the next safe point.
            </p>
          ) : null}
          {canRecover ? (
            <button
              className="warn"
              type="button"
              disabled={recoverMutation.isPending}
              onClick={() => recoverMutation.mutate()}
            >
              Retry / Recover
            </button>
          ) : null}
          {canCancel ? (
            <button
              className="danger"
              type="button"
              disabled={cancelMutation.isPending}
              onClick={() => cancelMutation.mutate()}
            >
              Cancel
            </button>
          ) : null}
          {canArchive ? (
            <button
              type="button"
              disabled={archiveMutation.isPending}
              onClick={() => archiveMutation.mutate(true)}
            >
              Archive
            </button>
          ) : null}
          {canUnarchive ? (
            <button
              type="button"
              disabled={archiveMutation.isPending}
              onClick={() => archiveMutation.mutate(false)}
            >
              Restore
            </button>
          ) : null}
          {canDelete && !confirmDelete ? (
            <button
              className="danger"
              type="button"
              onClick={() => setConfirmDelete(true)}
            >
              Delete
            </button>
          ) : null}
          {canDelete && confirmDelete ? (
            <>
              <button
                className="danger"
                type="button"
                disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate()}
              >
                Delete permanently
              </button>
              <button type="button" onClick={() => setConfirmDelete(false)}>
                Keep
              </button>
            </>
          ) : null}
          {!canRun &&
          !canPause &&
          !canRecover &&
          !canCancel &&
          !canArchive &&
          !canUnarchive &&
          !canDelete ? (
            <p className="empty" style={{ margin: 0 }}>
              No executive actions available in this state.
            </p>
          ) : null}
        </LifecycleControls>
        {irreversibleDone ? (
          <p className="meta" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
            Finished steps stay done. Pause and cancel do not undo them.
          </p>
        ) : null}
        {waitingNotRunning ? (
          <p className="meta" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
            Decide in the centre first. Start and Resume stay hidden until I can
            actually continue.
          </p>
        ) : null}
      </WorkspaceRailSection>
    </Panel>
  )

  const context = (
    <div className="stack">
      <Panel
        title="Outcome"
        tone={detail.status === 'failed' ? 'failed' : undefined}
      >
        {detail.status === 'completed' ? (
          <p style={{ marginTop: 0 }}>Done — here's what changed.</p>
        ) : detail.status === 'failed' ? (
          <p style={{ marginTop: 0 }} className="error-text">
            I couldn't finish this. Inspect activity and recover if needed.
          </p>
        ) : detail.phase === 'unavailable' ? (
          <p style={{ marginTop: 0 }} className="error-text">
            {detail.blocking?.message || "I can't do this yet."}
          </p>
        ) : waitingNotRunning ? (
          <p style={{ marginTop: 0 }}>
            {detail.blocking?.message ||
              'I need your decision before I continue. Nothing is running.'}
          </p>
        ) : (
          <p style={{ marginTop: 0 }} className="meta">
            {detail.blocking?.message || "I'm taking care of this."}
          </p>
        )}
        <StepProgress steps={detail.steps} />
      </Panel>

      <Panel title="Artifacts">
        <div className="scroll-panel">
          {detail.artifacts.map((item) => (
            <details
              key={String(item.id)}
              className="list-row"
              style={{ display: 'block' }}
            >
              <summary>
                <strong>{String(item.kind)}</strong>
                <div className="meta">Open preview</div>
              </summary>
              <Inspect label="Inspect artifact">
                {JSON.stringify(item.payload, null, 2)}
              </Inspect>
            </details>
          ))}
          {!detail.artifacts.length ? (
            <p className="empty">No artifacts yet.</p>
          ) : null}
        </div>
      </Panel>

      <Panel title="Evidence">
        <div className="scroll-panel">
          {detail.claims.map((claim) => (
            <div key={String(claim.id)} className="list-row">
              <div>
                <strong>{String(claim.subject)}</strong>
                <div className="meta">{String(claim.kind)}</div>
              </div>
            </div>
          ))}
          {detail.executions.map((execution) => (
            <div key={String(execution.id)} className="list-row">
              <div>
                <strong>{String(execution.capability)}</strong>
                <div className="meta">
                  Attempt {String(execution.attempt)} ·{' '}
                  {String(execution.status)}
                  {execution.error ? ` · ${String(execution.error)}` : ''}
                </div>
              </div>
              <Chip tone={stepTone(String(execution.status))}>
                {String(execution.status)}
              </Chip>
            </div>
          ))}
          {!detail.claims.length && !detail.executions.length ? (
            <p className="empty">Evidence appears as work completes.</p>
          ) : null}
        </div>
      </Panel>

      <Panel title="Activity">
        <div className="scroll-panel">
          {[...detail.events].reverse().map((event) => (
            <div key={String(event.id)} className="list-row">
              <div>
                <strong>{humanEventName(String(event.name))}</strong>
                <div className="meta">{String(event.created_at)}</div>
              </div>
            </div>
          ))}
          {!detail.events.length ? (
            <p className="empty">No activity yet.</p>
          ) : null}
        </div>
        <Inspect label="Inspect technical details">
          {JSON.stringify(
            {
              work_id: detail.work_id,
              phase: detail.phase,
              status: detail.status,
              contract: detail.contract,
              capabilities: detail.capabilities,
              events: detail.events,
            },
            null,
            2,
          )}
        </Inspect>
      </Panel>
    </div>
  )

  return (
    <Workspace
      title={detail.objective}
      crumb={
        <>
          <Link to="/work">Work</Link> / Current
        </>
      }
      subtitle={
        waitingNotRunning
          ? detail.blocking?.message ||
            'Atlas is waiting for your decision — nothing is executing.'
          : detail.blocking?.message ||
            'Track progress, decisions, artifacts, and outcome.'
      }
      headerActions={
        <>
          <Chip tone={chip.tone}>{chip.label}</Chip>
          {detail.phase === 'waiting_confirmation' ? (
            <Chip tone="confirm">Needs confirmation</Chip>
          ) : null}
          {detail.phase === 'waiting_authority' ? (
            <Chip tone="auth">Needs authority approval</Chip>
          ) : null}
        </>
      }
      railLabel="Controls"
      contextLabel="Details"
      rail={rail}
      context={context}
    >
      <div className="stack">
        {(detail.pending_approvals.length > 0 ||
          detail.pending_confirmations.length > 0) && (
          <div className="stack">
            {detail.pending_approvals.map((item) => (
              <AuthorityCard
                key={item.id}
                item={item}
                onDone={() =>
                  queryClient.invalidateQueries({
                    queryKey: ['work-detail', workId],
                  })
                }
              />
            ))}
            {detail.pending_confirmations.map((item) => (
              <ConfirmationCard
                key={item.id}
                item={item}
                onDone={() =>
                  queryClient.invalidateQueries({
                    queryKey: ['work-detail', workId],
                  })
                }
              />
            ))}
          </div>
        )}

        <Panel title="Progress">
          <StepProgress steps={detail.steps} />
          <div className="timeline" style={{ marginTop: '0.85rem' }}>
            {detail.steps.map((step) => (
              <div
                key={step.id}
                className={`t-item ${
                  step.status === 'pass' || step.status === 'skipped'
                    ? 'done'
                    : step.status === 'blocked'
                      ? 'waiting'
                      : step.status === 'failed'
                        ? 'failed'
                        : step.status === 'pending'
                          ? 'pending'
                          : ''
                }`}
              >
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: '0.75rem',
                    flexWrap: 'wrap',
                  }}
                >
                  <div>
                    <strong>{step.description}</strong>
                    <div className="meta">
                      {step.capability
                        ? humanCapabilityLabel(step.capability)
                        : 'Step'}
                    </div>
                  </div>
                  <Chip tone={stepTone(step.status)}>
                    {stepLabel(step.status)}
                  </Chip>
                </div>
              </div>
            ))}
            {!detail.steps.length ? (
              <p className="empty">No steps yet.</p>
            ) : null}
          </div>
        </Panel>
      </div>
    </Workspace>
  )
}

function AuthorityCard({
  item,
  onDone,
}: {
  item: WorkDetailType['pending_approvals'][number]
  onDone: () => void
}) {
  const approve = useMutation({
    mutationFn: () =>
      api(`/api/work/approvals/${item.id}/approve`, {
        method: 'POST',
        body: JSON.stringify({ note: 'approved in Companion' }),
      }),
    onSuccess: onDone,
  })
  const deny = useMutation({
    mutationFn: () =>
      api(`/api/work/approvals/${item.id}/deny`, {
        method: 'POST',
        body: JSON.stringify({ note: 'denied in Companion' }),
      }),
    onSuccess: onDone,
  })
  return (
    <Panel title="Authority approval" tone="decision-auth">
      <p style={{ marginTop: 0 }}>
        Atlas needs <strong>{item.required_authority}</strong> authority before
        continuing.
      </p>
      <p className="meta">{item.requested_action}</p>
      <div className="actions">
        <button
          className="authority"
          type="button"
          disabled={approve.isPending}
          onClick={() => approve.mutate()}
        >
          Approve authority
        </button>
        <button
          className="danger"
          type="button"
          disabled={deny.isPending}
          onClick={() => deny.mutate()}
        >
          Deny authority
        </button>
      </div>
    </Panel>
  )
}

function ConfirmationCard({
  item,
  onDone,
}: {
  item: WorkDetailType['pending_confirmations'][number]
  onDone: () => void
}) {
  const confirm = useMutation({
    mutationFn: () =>
      api(`/api/work/confirmations/${item.id}/confirm`, {
        method: 'POST',
        body: '{}',
      }),
    onSuccess: onDone,
  })
  const deny = useMutation({
    mutationFn: () =>
      api(`/api/work/confirmations/${item.id}/deny`, {
        method: 'POST',
        body: '{}',
      }),
    onSuccess: onDone,
  })
  const cancel = useMutation({
    mutationFn: () =>
      api(`/api/work/confirmations/${item.id}/cancel`, {
        method: 'POST',
        body: '{}',
      }),
    onSuccess: onDone,
  })
  const invocation =
    item.payload && typeof item.payload.invocation_input === 'object'
      ? (item.payload.invocation_input as Record<string, unknown>)
      : null

  return (
    <Panel title="Payload confirmation" tone="decision-confirm">
      <p style={{ marginTop: 0, fontSize: '1.05rem', lineHeight: 1.5 }}>
        {item.summary}
      </p>
      {invocation ? (
        <div
          className="card"
          style={{
            boxShadow: 'none',
            background: 'var(--atlas-bg)',
            margin: '0.75rem 0 1rem',
          }}
        >
          <div className="meta" style={{ marginBottom: '0.35rem' }}>
            Message preview
          </div>
          {Object.entries(invocation).map(([key, value]) => (
            <div key={key} className="brief-row">
              <span>{key}</span>
              <div>
                {typeof value === 'string' ? value : JSON.stringify(value)}
              </div>
            </div>
          ))}
        </div>
      ) : null}
      <div className="actions">
        <button
          className="confirm"
          type="button"
          disabled={confirm.isPending}
          onClick={() => confirm.mutate()}
        >
          Confirm payload
        </button>
        <button
          className="danger"
          type="button"
          disabled={deny.isPending}
          onClick={() => deny.mutate()}
        >
          Deny confirmation
        </button>
        <button
          type="button"
          disabled={cancel.isPending}
          onClick={() => cancel.mutate()}
        >
          Cancel confirmation
        </button>
      </div>
      <Inspect label="Inspect exact payload">
        {JSON.stringify(item.payload, null, 2)}
      </Inspect>
    </Panel>
  )
}

function stepLabel(status: string) {
  if (status === 'pass') return 'Done'
  if (status === 'blocked') return 'Waiting'
  if (status === 'running') return 'Running'
  if (status === 'failed') return 'Failed'
  if (status === 'skipped') return 'Skipped'
  if (status === 'rework') return 'Rework'
  return 'Pending'
}

function humanEventName(name: string) {
  const map: Record<string, string> = {
    'work.started': 'I started this',
    'work.paused': 'I paused at a safe point',
    'work.pause_requested': 'You asked me to pause',
    'work.resumed': 'I continued',
    'work.completed': 'Done',
    'work.failed': "I couldn't finish",
    'work.archived': 'Archived',
    'work.unarchived': 'Restored from archive',
    'confirmation.requested': 'I need your confirmation',
    'confirmation.applied': 'Confirmation received',
    'approval.requested': 'I need your approval',
    'approval.applied': 'Approval received',
    'capability.completed': 'Step completed',
    'capability.started': 'Step started',
  }
  return map[name] || name.replace(/\./g, ' ')
}
