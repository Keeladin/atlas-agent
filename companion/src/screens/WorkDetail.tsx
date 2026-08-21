import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkDetail as WorkDetailType } from '../api/types'
import { phaseChip, stepTone } from '../lib/workLabels'
import { Chip } from '../ui/Chip'
import { Inspect } from '../ui/Inspect'
import { Panel } from '../ui/Panel'
import { StepProgress } from '../ui/StepProgress'

export function WorkDetail() {
  const { workId = '' } = useParams()
  const queryClient = useQueryClient()
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
    const events = detailQuery.data?.events || []
    const after = events.length ? String(events[events.length - 1].id) : '0'
    const source = new EventSource(
      `/api/work/${workId}/events/stream?after=${after}`,
      { withCredentials: true },
    )
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: ['work-detail', workId] })
      void queryClient.invalidateQueries({ queryKey: ['work'] })
    }
    source.addEventListener('confirmation.requested', refresh)
    source.addEventListener('confirmation.applied', refresh)
    source.addEventListener('approval.requested', refresh)
    source.addEventListener('approval.applied', refresh)
    source.addEventListener('work.paused', refresh)
    source.addEventListener('work.completed', refresh)
    source.addEventListener('work.failed', refresh)
    source.addEventListener('capability.completed', refresh)
    source.onmessage = refresh
    return () => source.close()
  }, [workId, queryClient, detailQuery.data?.events?.length])

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

  return (
    <div className="stack">
      <div className="topbar">
        <div>
          <div className="crumb">
            <Link to="/work">Work</Link> / Current
          </div>
          <h1>{detail.objective}</h1>
          <p>
            {waitingNotRunning
              ? detail.blocking?.message ||
                'Atlas is waiting for your decision — nothing is executing.'
              : detail.blocking?.message ||
                'Track progress, decisions, artifacts, and outcome.'}
          </p>
          <div className="actions" style={{ marginTop: '0.55rem' }}>
            <Chip tone={chip.tone}>{chip.label}</Chip>
            {detail.phase === 'waiting_confirmation' ? (
              <Chip tone="confirm">Needs confirmation</Chip>
            ) : null}
            {detail.phase === 'waiting_authority' ? (
              <Chip tone="auth">Needs authority approval</Chip>
            ) : null}
          </div>
        </div>
        <div className="actions">
          {detail.actions.includes('run') ? (
            <button
              className="primary"
              type="button"
              disabled={runMutation.isPending}
              onClick={() => runMutation.mutate()}
            >
              Continue
            </button>
          ) : null}
          {detail.actions.includes('recover') ? (
            <button
              className="warn"
              type="button"
              disabled={recoverMutation.isPending}
              onClick={() => recoverMutation.mutate()}
            >
              Recover
            </button>
          ) : null}
          {detail.actions.includes('cancel') ? (
            <button
              className="danger"
              type="button"
              disabled={cancelMutation.isPending}
              onClick={() => cancelMutation.mutate()}
            >
              Cancel work
            </button>
          ) : null}
        </div>
      </div>

      <div className="grid-2">
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
                        {step.capability || 'Step'}
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

        <div className="stack">
          <Panel
            title="Outcome"
            tone={detail.status === 'failed' ? 'failed' : undefined}
          >
            {detail.status === 'completed' ? (
              <p style={{ marginTop: 0 }}>Work finished successfully.</p>
            ) : detail.status === 'failed' ? (
              <p style={{ marginTop: 0 }} className="error-text">
                Work failed. Inspect activity and recover if needed.
              </p>
            ) : waitingNotRunning ? (
              <p style={{ marginTop: 0 }}>
                Paused for your decision. No execution is running.
              </p>
            ) : (
              <p style={{ marginTop: 0 }} className="meta">
                {detail.blocking?.message || 'In progress.'}
              </p>
            )}
            <StepProgress steps={detail.steps} />
          </Panel>

          <Panel title="Artifacts">
            <div className="scroll-panel">
              {detail.artifacts.map((item) => (
                <details key={String(item.id)} className="list-row" style={{ display: 'block' }}>
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
                      Attempt {String(execution.attempt)} · {String(execution.status)}
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
      </div>
    </div>
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
          style={{ boxShadow: 'none', background: '#0a1020', margin: '0.75rem 0 1rem' }}
        >
          <div className="meta" style={{ marginBottom: '0.35rem' }}>
            Message preview
          </div>
          {Object.entries(invocation).map(([key, value]) => (
            <div key={key} className="brief-row">
              <span>{key}</span>
              <div>{typeof value === 'string' ? value : JSON.stringify(value)}</div>
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
    'work.started': 'Work started',
    'work.paused': 'Work paused',
    'work.completed': 'Work completed',
    'work.failed': 'Work failed',
    'confirmation.requested': 'Confirmation requested',
    'confirmation.applied': 'Confirmation applied',
    'approval.requested': 'Authority approval requested',
    'approval.applied': 'Authority approval applied',
    'capability.completed': 'Step completed',
    'capability.started': 'Step started',
  }
  return map[name] || name.replace(/\./g, ' ')
}
