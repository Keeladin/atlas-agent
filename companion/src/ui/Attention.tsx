import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { WorkItem } from '../api/types'
import { StatusLamp } from './OperationsPrimitives'
import { Sheet } from './Sheet'

export function Attention() {
  const [open, setOpen] = useState(false)
  const query = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work'), refetchInterval: 5000 })
  const items = (query.data?.work ?? []).filter(item => ['failed', 'paused'].includes(item.status))
  return <>
    <button type="button" className={`attention-trigger${items.length ? ' has-items' : ''}`} onClick={() => setOpen(true)}>
      <span className="attention-trigger-label">Needs you</span>{items.length ? <span className="attention-badge">{items.length}</span> : null}
    </button>
    {open ? <Sheet title="Needs you" onClose={() => setOpen(false)}>
      {!items.length ? <p className="empty">Nothing needs you right now.</p> : null}
      <div className="stack">{items.map(item => <Link className="surface-margin-row" key={item.work_id} to={`/work/${item.work_id}`} onClick={() => setOpen(false)}><StatusLamp tone="red" /><span><strong>{item.display_ref ?? item.objective}</strong><small>{item.objective}</small><em>{item.status}</em></span></Link>)}</div>
    </Sheet> : null}
  </>
}
