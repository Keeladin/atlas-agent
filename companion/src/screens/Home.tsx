import { useQueries, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Conversation, WorkDetail, WorkListItem } from '../api/types'
import { humanWorkStatus } from '../lib/workLabels'
import { Chip } from '../ui/Chip'
import { Panel } from '../ui/Panel'

export function Home() {
  const workQuery = useQuery({
    queryKey: ['work'],
    queryFn: () => api<{ work: WorkListItem[] }>('/api/work'),
  })
  const chatQuery = useQuery({
    queryKey: ['conversations'],
    queryFn: () =>
      api<{ conversations: Conversation[] }>('/api/chat/conversations'),
  })

  const openItems = (workQuery.data?.work || []).filter((item) =>
    ['planned', 'active', 'waiting'].includes(item.status),
  )
  const detailQueries = useQueries({
    queries: openItems.slice(0, 8).map((item) => ({
      queryKey: ['work-detail', item.work_id],
      queryFn: () => api<WorkDetail>(`/api/work/${item.work_id}/detail`),
    })),
  })
  const details = detailQueries
    .map((q) => q.data)
    .filter((item): item is WorkDetail => Boolean(item))

  const needsYou = details.filter(
    (d) =>
      d.pending_confirmations.length > 0 ||
      d.pending_approvals.length > 0 ||
      d.actions.includes('recover'),
  )
  const inMotion = details.filter(
    (d) =>
      d.phase === 'running' ||
      d.phase === 'active' ||
      (d.status === 'active' &&
        !d.pending_confirmations.length &&
        !d.pending_approvals.length),
  )
  const doneToday = (workQuery.data?.work || []).filter(
    (item) => item.status === 'completed',
  ).length

  const offline = workQuery.isError || chatQuery.isError

  return (
    <div className="stack">
      {offline ? (
        <div className="offline-banner">
          Atlas host unreachable or session expired. Some panels may be stale.
        </div>
      ) : null}

      <div className="topbar">
        <div>
          <h1>Home</h1>
          <p>
            {needsYou.length
              ? `Atlas is holding ${needsYou.length} item${needsYou.length === 1 ? '' : 's'} that need you.`
              : 'Nothing needs your attention right now.'}
          </p>
        </div>
        <div className="actions">
          <Link to="/chat">
            <button type="button">Continue chat</button>
          </Link>
          <Link to="/work/new">
            <button className="primary" type="button">
              Start work
            </button>
          </Link>
        </div>
      </div>

      <div className="grid-3">
        <Panel className="kpi">
          <div className="value" style={{ color: '#ffd978' }}>
            {needsYou.length}
          </div>
          <div className="label">Needs you</div>
        </Panel>
        <Panel className="kpi">
          <div className="value" style={{ color: '#93c5fd' }}>
            {inMotion.length}
          </div>
          <div className="label">In motion</div>
        </Panel>
        <Panel className="kpi">
          <div className="value" style={{ color: '#6ee7b7' }}>
            {doneToday}
          </div>
          <div className="label">Completed work</div>
        </Panel>
      </div>

      <div className="grid-2">
        <div className="stack">
          <Panel title="Needs you" tone="attention">
            {!needsYou.length ? (
              <p className="empty">No decisions waiting.</p>
            ) : (
              needsYou.map((item) => {
                const chip = humanWorkStatus(item)
                const reason = item.pending_confirmations[0]?.summary
                  || item.pending_approvals[0]?.requested_action
                  || item.blocking?.message
                  || 'Needs your attention'
                return (
                  <Link
                    key={item.work_id}
                    to={`/work/${item.work_id}`}
                    className="list-row"
                  >
                    <div>
                      <strong>{item.objective}</strong>
                      <div className="meta">{reason}</div>
                    </div>
                    <Chip tone={chip.tone}>{chip.label}</Chip>
                  </Link>
                )
              })
            )}
          </Panel>

          <Panel title="In motion">
            {!inMotion.length ? (
              <p className="empty">Nothing running.</p>
            ) : (
              inMotion.map((item) => (
                <Link
                  key={item.work_id}
                  to={`/work/${item.work_id}`}
                  className="list-row"
                >
                  <div>
                    <strong>{item.objective}</strong>
                    <div className="meta">
                      {item.blocking?.message || 'Work is underway'}
                    </div>
                  </div>
                  <Chip tone="running">In progress</Chip>
                </Link>
              ))
            )}
          </Panel>
        </div>

        <div className="stack">
          <Panel title="Jump back in">
            {(chatQuery.data?.conversations || []).slice(0, 3).map((item) => (
              <Link key={item.id} to="/chat" className="list-row">
                <div>
                  <strong>{item.title}</strong>
                  <div className="meta">{item.turn_count} messages</div>
                </div>
                <span className="meta">Open</span>
              </Link>
            ))}
            {openItems.slice(0, 3).map((item) => {
              const chip = humanWorkStatus(item)
              return (
                <Link
                  key={item.work_id}
                  to={`/work/${item.work_id}`}
                  className="list-row"
                >
                  <div>
                    <strong>{item.objective}</strong>
                    <div className="meta">Open work</div>
                  </div>
                  <Chip tone={chip.tone}>{chip.label}</Chip>
                </Link>
              )
            })}
            {!chatQuery.data?.conversations?.length && !openItems.length ? (
              <p className="empty">Start a chat or plan work to begin.</p>
            ) : null}
          </Panel>

          <Panel title="Quick starts">
            <div className="actions" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
              <Link to="/chat">
                <button type="button" style={{ width: '100%' }}>
                  Ask Atlas anything
                </button>
              </Link>
              <Link to="/work/new">
                <button type="button" style={{ width: '100%' }}>
                  Turn an idea into work
                </button>
              </Link>
              <Link to="/files">
                <button type="button" style={{ width: '100%' }}>
                  Index a file into knowledge
                </button>
              </Link>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}
