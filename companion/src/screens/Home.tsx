import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkListItem } from '../api/types'
import { Panel } from '../ui/Panel'
import { StatusChip } from '../ui/StatusChip'

export function Home() {
  const workQuery = useQuery({
    queryKey: ['work'],
    queryFn: () => api<{ work: WorkListItem[] }>('/api/work'),
  })
  const waiting = (workQuery.data?.work || []).filter((item) =>
    ['waiting', 'active', 'planned'].includes(item.status),
  )

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <div>
        <h1 style={{ margin: 0 }}>Home</h1>
        <p style={{ color: 'var(--text-muted)' }}>
          Companion is a client of Chat, Advanced, and Work. Open Work for confirmations and authority.
        </p>
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <Link to="/chat">
          <button type="button">Open Chat</button>
        </Link>
        <Link to="/work/new">
          <button className="primary" type="button">
            Plan Work
          </button>
        </Link>
        <Link to="/work">
          <button type="button">Work cockpit</button>
        </Link>
      </div>
      <Panel title="Open Work">
        <div style={{ display: 'grid', gap: '0.6rem' }}>
          {waiting.map((item) => (
            <Link
              key={item.work_id}
              to={`/work/${item.work_id}`}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: '0.75rem',
                padding: '0.75rem',
                borderRadius: 12,
                border: '1px solid var(--border)',
              }}
            >
              <span>{item.objective}</span>
              <StatusChip value={item.status} />
            </Link>
          ))}
          {!waiting.length ? (
            <p style={{ color: 'var(--text-muted)' }}>Nothing waiting.</p>
          ) : null}
        </div>
      </Panel>
    </div>
  )
}
