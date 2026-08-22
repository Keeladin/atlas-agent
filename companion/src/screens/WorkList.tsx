import { useQueries, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkDetail, WorkListItem } from '../api/types'
import { humanWorkStatus } from '../lib/workLabels'
import { Chip } from '../ui/Chip'
import { Panel } from '../ui/Panel'
import { StepProgress } from '../ui/StepProgress'
import { Workspace, WorkspaceRailSection } from '../ui/Workspace'

type Filter = 'all' | 'needs_you' | 'in_progress' | 'waiting' | 'done' | 'failed' | 'archived'

export function WorkList() {
  const [filter, setFilter] = useState<Filter>('all')
  const query = useQuery({
    queryKey: ['work', filter === 'archived'],
    queryFn: () =>
      api<{ work: WorkListItem[] }>(
        filter === 'archived' ? '/api/work?archived=true' : '/api/work',
      ),
  })
  const items = query.data?.work || []
  const details = useQueries({
    queries: items.map((item) => ({
      queryKey: ['work-detail', item.work_id],
      queryFn: () => api<WorkDetail>(`/api/work/${item.work_id}/detail`),
    })),
  })
  const byId = useMemo(() => {
    const map = new Map<string, WorkDetail>()
    details.forEach((entry, index) => {
      if (entry.data) map.set(items[index].work_id, entry.data)
    })
    return map
  }, [details, items])

  const filtered = items.filter((item) => {
    const detail = byId.get(item.work_id)
    const phase = detail?.phase
    if (filter === 'all') return true
    if (filter === 'done') return item.status === 'completed'
    if (filter === 'failed') return item.status === 'failed'
    if (filter === 'needs_you') {
      return (
        phase === 'waiting_confirmation' ||
        phase === 'waiting_authority' ||
        Boolean(detail?.pending_confirmations.length) ||
        Boolean(detail?.pending_approvals.length)
      )
    }
    if (filter === 'in_progress') {
      return phase === 'running' || phase === 'active' || item.status === 'active'
    }
    if (filter === 'waiting') {
      return item.status === 'waiting' || item.status === 'planned'
    }
    return true
  })

  const rail = (
    <Panel>
      <div className="workspace-rail-actions">
        <Link to="/work/new">
          <button className="primary" type="button">
            Plan new work
          </button>
        </Link>
      </div>
      <WorkspaceRailSection title="Filters">
        <div className="filters" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
          {(
            [
              ['all', 'All'],
              ['needs_you', 'Needs you'],
              ['in_progress', 'In progress'],
              ['waiting', 'Waiting'],
              ['done', 'Done'],
              ['failed', 'Failed'],
              ['archived', 'Archived'],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={filter === id ? 'active' : ''}
              onClick={() => setFilter(id)}
            >
              {label}
            </button>
          ))}
        </div>
      </WorkspaceRailSection>
    </Panel>
  )

  const context = (
    <Panel title="At a glance">
      <div className="list-row">
        <div>
          <strong>{items.length}</strong>
          <div className="meta">Total work items</div>
        </div>
      </div>
      <div className="list-row">
        <div>
          <strong>{filtered.length}</strong>
          <div className="meta">In this filter</div>
        </div>
      </div>
      <p className="meta" style={{ marginBottom: 0 }}>
        Open an item to review progress, decisions, and outcomes.
      </p>
    </Panel>
  )

  return (
    <Workspace
      title="Work"
      subtitle="Durable responsibilities Atlas is carrying — progress, waits, and outcomes at a glance."
      railLabel="Browse"
      contextLabel="Summary"
      rail={rail}
      context={context}
      banner={
        query.isError ? (
          <div className="offline-banner">Could not load work from the Atlas host.</div>
        ) : null
      }
    >
      <div className="stack">
        {query.isLoading ? <p className="empty">Loading…</p> : null}
        {filtered.map((item) => {
          const detail = byId.get(item.work_id)
          const chip = humanWorkStatus({
            status: item.status,
            phase: detail?.phase,
          })
          const subtitle =
            detail?.pending_confirmations[0]?.summary ||
            detail?.pending_approvals[0]?.requested_action ||
            detail?.blocking?.message ||
            `Authority ${item.authority_scope}`
          return (
            <Link
              key={item.work_id}
              to={`/work/${item.work_id}`}
              className="card"
              style={{ display: 'block' }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  gap: '1rem',
                  flexWrap: 'wrap',
                  alignItems: 'flex-start',
                }}
              >
                <div>
                  <h2 style={{ margin: '0 0 0.35rem', fontSize: '1.05rem' }}>
                    {item.objective}
                  </h2>
                  <div className="meta">{subtitle}</div>
                </div>
                <Chip tone={chip.tone}>{chip.label}</Chip>
              </div>
              {detail?.steps?.length ? (
                <div style={{ marginTop: '0.85rem' }}>
                  <StepProgress steps={detail.steps} />
                </div>
              ) : null}
            </Link>
          )
        })}
        {!query.isLoading && !filtered.length ? (
          <Panel>
            <p className="empty">No work in this view.</p>
          </Panel>
        ) : null}
      </div>
    </Workspace>
  )
}
