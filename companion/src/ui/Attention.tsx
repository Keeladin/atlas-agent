import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import type { ActionOccurrence } from '../api/types'
import { ConfirmationCard } from './ConfirmationCard'
import { Sheet } from './Sheet'

export function Attention() {
  const [open, setOpen] = useState(false)
  const query = useQuery({ queryKey: ['pending-actions'], queryFn: () => api<{ actions: ActionOccurrence[] }>('/api/actions/pending'), refetchInterval: 5000 })
  const items = query.data?.actions ?? []
  return <>
    <button type="button" className={`attention-trigger${items.length ? ' has-items' : ''}`} onClick={() => setOpen(true)}>
      <span className="attention-trigger-label">Needs you</span>{items.length ? <span className="attention-badge">{items.length}</span> : null}
    </button>
    {open ? <Sheet title="Needs you" onClose={() => setOpen(false)}>
      {!items.length ? <p className="empty">Nothing needs you right now.</p> : null}
      <div className="stack">{items.map(item => <ConfirmationCard key={item.occurrence_id} item={item} onDone={() => query.refetch()} />)}</div>
    </Sheet> : null}
  </>
}
