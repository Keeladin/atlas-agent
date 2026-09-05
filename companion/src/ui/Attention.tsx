import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { AttentionItem } from '../api/types'
import { attentionDetail, attentionHref, attentionStatus, attentionTitle, attentionTone } from './attentionState'
import { StatusLamp } from './OperationsPrimitives'
import { Sheet } from './Sheet'

export function Attention() {
  const [open, setOpen] = useState(false)
  const query = useQuery({
    queryKey: ['attention'],
    queryFn: () => api<{ attention: AttentionItem[] }>('/api/attention'),
    refetchInterval: 5000,
  })
  const items = query.data?.attention ?? []
  return <>
    <button type="button" className={`attention-trigger${items.length ? ' has-items' : ''}`} onClick={() => setOpen(true)}>
      <span className="attention-trigger-label">Needs you</span>{items.length ? <span className="attention-badge">{items.length}</span> : null}
    </button>
    {open ? <Sheet title="Needs you" onClose={() => setOpen(false)}>
      {!items.length ? <p className="empty">Nothing needs you right now.</p> : null}
      <div className="stack">{items.map(item => <Link className="surface-margin-row" key={`${item.kind}:${item.obligation_id}`} to={attentionHref(item)} onClick={() => setOpen(false)}><StatusLamp tone={attentionTone(item)} /><span><strong>{attentionTitle(item)}</strong><small>{attentionDetail(item)}</small><em>{attentionStatus(item)}</em></span></Link>)}</div>
    </Sheet> : null}
  </>
}
