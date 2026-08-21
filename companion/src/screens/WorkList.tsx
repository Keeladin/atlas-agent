import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkListItem } from '../api/types'
import { Panel } from '../ui/Panel'
import { StatusChip } from '../ui/StatusChip'

export function WorkList() {
  const query = useQuery({
    queryKey: ['work'],
    queryFn: () => api<{ work: WorkListItem[] }>('/api/work'),
  })

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          gap: '1rem',
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        <div>
          <h1 style={{ margin: 0 }}>Work</h1>
          <p style={{ color: 'var(--text-muted)', margin: '0.35rem 0 0' }}>
            Durable WorkRuntime items. Authority and confirmation stay on the detail cockpit.
          </p>
        </div>
        <Link to="/work/new">
          <button className="primary" type="button">
            Plan Work
          </button>
        </Link>
      </div>
      <Panel>
        {query.isLoading ? <p>Loading…</p> : null}
        {query.error ? (
          <p style={{ color: 'var(--danger)' }}>
            {(query.error as Error).message}
          </p>
        ) : null}
        <div style={{ display: 'grid', gap: '0.75rem' }}>
          {(query.data?.work || []).map((item) => (
            <Link
              key={item.work_id}
              to={`/work/${item.work_id}`}
              style={{
                display: 'grid',
                gap: '0.35rem',
                padding: '0.9rem',
                borderRadius: 12,
                border: '1px solid var(--border)',
                background: 'var(--bg-elevated)',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  gap: '0.75rem',
                  flexWrap: 'wrap',
                }}
              >
                <strong>{item.objective}</strong>
                <StatusChip value={item.status} />
              </div>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                {item.work_id} · authority {item.authority_scope}
              </div>
            </Link>
          ))}
          {!query.isLoading && (query.data?.work || []).length === 0 ? (
            <p style={{ color: 'var(--text-muted)' }}>No work yet.</p>
          ) : null}
        </div>
      </Panel>
    </div>
  )
}
