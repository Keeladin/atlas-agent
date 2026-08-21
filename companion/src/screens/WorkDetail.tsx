import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkDetail as WorkDetailType } from '../api/types'
import { Panel } from '../ui/Panel'
import { StatusChip } from '../ui/StatusChip'

export function WorkDetail() {
  const { workId = '' } = useParams()
  const queryClient = useQueryClient()
  const detailQuery = useQuery({
    queryKey: ['work-detail', workId],
    queryFn: () => api<WorkDetailType>(`/api/work/${workId}/detail`),
    enabled: Boolean(workId),
    refetchInterval: (query) => {
      const phase = query.state.data?.phase
      return phase === 'running' || phase === 'active' ? 2000 : false
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
    }
    source.onmessage = refresh
    source.addEventListener('confirmation.requested', refresh)
    source.addEventListener('confirmation.applied', refresh)
    source.addEventListener('approval.requested', refresh)
    source.addEventListener('approval.applied', refresh)
    source.addEventListener('work.paused', refresh)
    source.addEventListener('work.completed', refresh)
    source.addEventListener('work.failed', refresh)
    source.addEventListener('capability.completed', refresh)
    return () => source.close()
  }, [workId, queryClient, detailQuery.data?.events?.length])

  const runMutation = useMutation({
    mutationFn: () => api(`/api/work/${workId}/run`, { method: 'POST', body: '{}' }),
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

  if (detailQuery.isLoading) return <p>Loading work…</p>
  if (detailQuery.error) {
    return (
      <p style={{ color: 'var(--danger)' }}>
        {(detailQuery.error as Error).message}
      </p>
    )
  }
  if (!detail) return null

  const phaseLabel =
    detail.phase === 'waiting_confirmation'
      ? 'waiting · confirmation'
      : detail.phase === 'waiting_authority'
        ? 'waiting · authority'
        : detail.phase

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
        <div>
          <Link to="/work" style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
            ← Work
          </Link>
          <h1 style={{ margin: '0.35rem 0' }}>{detail.objective}</h1>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            <StatusChip value={detail.status} />
            <StatusChip value={detail.phase} label={phaseLabel} />
            <StatusChip value={detail.authority_scope} label={`authority ${detail.authority_scope}`} />
          </div>
          <p style={{ color: 'var(--text-muted)', marginBottom: 0 }}>
            {detail.work_id}
            {detail.blocking ? ` · ${detail.blocking.message}` : ''}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          {detail.actions.includes('run') ? (
            <button
              className="primary"
              type="button"
              disabled={runMutation.isPending}
              onClick={() => runMutation.mutate()}
            >
              Run
            </button>
          ) : null}
          {detail.actions.includes('recover') ? (
            <button
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
              Cancel
            </button>
          ) : null}
        </div>
      </div>

      {(detail.pending_approvals.length > 0 ||
        detail.pending_confirmations.length > 0) && (
        <Panel title="Needs you">
          <div style={{ display: 'grid', gap: '0.75rem' }}>
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
        </Panel>
      )}

      <Panel title="Contract">
        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'var(--mono)', fontSize: '0.85rem' }}>
          {JSON.stringify(detail.contract, null, 2)}
        </pre>
        <div style={{ marginTop: '0.75rem', display: 'grid', gap: '0.5rem' }}>
          {detail.capabilities.map((pin) => (
            <div
              key={String(pin.capability_id)}
              style={{
                border: '1px solid var(--border)',
                borderRadius: 10,
                padding: '0.65rem 0.8rem',
              }}
            >
              <strong>{String(pin.capability_id)}</strong>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                armed={String(pin.armed)} · confirmation={String(pin.confirmation)} ·{' '}
                {String(pin.executor_kind || '—')}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Steps">
        <div style={{ display: 'grid', gap: '0.5rem' }}>
          {detail.steps.map((step) => (
            <div
              key={step.id}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: '0.75rem',
                flexWrap: 'wrap',
                padding: '0.65rem 0',
                borderBottom: '1px solid var(--border)',
              }}
            >
              <div>
                <div>
                  <strong>
                    {step.ordinal}. {step.description}
                  </strong>
                </div>
                <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                  {step.capability} {step.capability_version || ''}
                </div>
              </div>
              <StatusChip value={step.status} />
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Artifacts">
        <ArtifactList items={detail.artifacts} />
      </Panel>

      <Panel title="Evidence">
        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'var(--mono)', fontSize: '0.85rem' }}>
          {JSON.stringify({ claims: detail.claims, executions: detail.executions }, null, 2)}
        </pre>
      </Panel>

      <Panel title="Events">
        <div style={{ display: 'grid', gap: '0.45rem' }}>
          {detail.events.map((event) => (
            <div
              key={String(event.id)}
              style={{
                fontFamily: 'var(--mono)',
                fontSize: '0.8rem',
                color: 'var(--text-muted)',
              }}
            >
              #{String(event.id)} {String(event.created_at)} · {String(event.name)}
            </div>
          ))}
        </div>
      </Panel>
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
    <Panel title="Authority approval" tone="authority">
      <p style={{ marginTop: 0 }}>
        Atlas needs <strong>{item.required_authority}</strong> authority.
      </p>
      <p style={{ color: 'var(--text-muted)' }}>{item.requested_action}</p>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button className="primary" type="button" onClick={() => approve.mutate()}>
          Approve authority
        </button>
        <button className="danger" type="button" onClick={() => deny.mutate()}>
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
  return (
    <Panel title="Payload confirmation" tone="confirmation">
      <p style={{ marginTop: 0, fontSize: '1.05rem' }}>{item.summary}</p>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
        {item.capability_id} · {item.payload_sha256.slice(0, 12)}…
      </p>
      <pre
        style={{
          margin: '0 0 0.75rem',
          whiteSpace: 'pre-wrap',
          fontFamily: 'var(--mono)',
          fontSize: '0.8rem',
          background: '#0d1524',
          padding: '0.75rem',
          borderRadius: 10,
        }}
      >
        {JSON.stringify(item.payload, null, 2)}
      </pre>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button className="primary" type="button" onClick={() => confirm.mutate()}>
          Confirm payload
        </button>
        <button className="danger" type="button" onClick={() => deny.mutate()}>
          Deny confirmation
        </button>
        <button type="button" onClick={() => cancel.mutate()}>
          Cancel confirmation
        </button>
      </div>
    </Panel>
  )
}

function ArtifactList({ items }: { items: Array<Record<string, unknown>> }) {
  if (!items.length) return <p style={{ color: 'var(--text-muted)' }}>No artifacts.</p>
  return (
    <div style={{ display: 'grid', gap: '0.5rem' }}>
      {items.map((item) => (
        <details
          key={String(item.id)}
          style={{
            border: '1px solid var(--border)',
            borderRadius: 10,
            padding: '0.55rem 0.75rem',
          }}
        >
          <summary>
            <strong>{String(item.kind)}</strong> · {String(item.id)}
          </summary>
          <pre
            style={{
              whiteSpace: 'pre-wrap',
              fontFamily: 'var(--mono)',
              fontSize: '0.8rem',
            }}
          >
            {JSON.stringify(item.payload, null, 2)}
          </pre>
        </details>
      ))}
    </div>
  )
}
