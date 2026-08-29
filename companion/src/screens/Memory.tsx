import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api } from '../api/client'
import type { ActionOccurrence, MemoryItem } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { Panel } from '../ui/Panel'
import { Workspace } from '../ui/Workspace'

export function Memory() {
  const qc = useQueryClient()
  const [query, setQuery] = useState('')
  const [pending, setPending] = useState<ActionOccurrence | null>(null)
  const memories = useQuery({
    queryKey: ['memory', query],
    queryFn: () => api<{ items: MemoryItem[] }>(`/api/memory${query ? `?q=${encodeURIComponent(query)}` : ''}`),
  })
  const act = useMutation({
    mutationFn: ({ itemId, action }: { itemId: string; action: 'retract' | 'restore' | 'purge' }) =>
      api<{ action: ActionOccurrence }>(`/api/memory/${itemId}/${action}`, { method: 'POST', body: '{}' }),
    onSuccess: async ({ action }) => {
      setPending(action.status === 'pending_confirmation' ? action : null)
      await qc.invalidateQueries({ queryKey: ['memory'] })
      await qc.invalidateQueries({ queryKey: ['pending-actions'] })
    },
  })
  const deleteSource = useMutation({
    mutationFn: (conversationId: string) => api(`/api/chat/conversations/${conversationId}`, { method: 'DELETE' }),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ['conversations'] }) },
  })
  const items = useMemo(() => memories.data?.items ?? [], [memories.data])

  return <Workspace title="Memory" subtitle="Durable owner memory, governed by the same live NO / YES / CONFIRM runtime policy as every consequential Atlas action.">
    <div className="stack">
      <Panel title="Persistent memory">
        <div className="knowledge-toolbar"><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search memory" /><span className="meta">{items.length} items</span></div>
        {memories.isError ? <p className="offline-banner">{memories.error.message}</p> : null}
        {act.isError ? <p className="offline-banner">{act.error.message}</p> : null}
        {!memories.isLoading && items.length === 0 ? <div className="empty-state compact"><strong>No matching memories</strong><span>Owner-grounded memories captured by Atlas will appear here.</span></div> : null}
        <div className="knowledge-list">{items.map(item => {
          const sourceConversation = typeof item.metadata?.source_conversation_id === 'string' ? item.metadata.source_conversation_id : null
          return <article className="knowledge-item" key={item.item_id}>
            <div className="row-head"><strong>{item.title || 'Memory'}</strong><span className={`chip ${item.state === 'active' ? 'done' : ''}`}>{item.state}</span></div>
            <p>{item.content}</p>
            {item.grounding_excerpt ? <details className="inspect"><summary>Owner grounding</summary><p>{item.grounding_excerpt}</p></details> : null}
            <div className="meta mono">{item.item_id}{item.supersedes ? ` · supersedes ${item.supersedes}` : ''}</div>
            <div className="actions">
              {item.state === 'active' ? <button type="button" onClick={() => act.mutate({ itemId: item.item_id, action: 'retract' })}>Retract</button> : null}
              {item.state === 'retracted' ? <button type="button" onClick={() => act.mutate({ itemId: item.item_id, action: 'restore' })}>Restore</button> : null}
              <button className="danger" type="button" onClick={() => act.mutate({ itemId: item.item_id, action: 'purge' })}>Purge</button>
              {sourceConversation ? <button type="button" onClick={() => { if (window.confirm('Delete the source conversation too? Memory purge cannot atomically remove chat turns from atlas-chat.db.')) deleteSource.mutate(sourceConversation) }}>Delete source conversation</button> : null}
            </div>
          </article>
        })}</div>
      </Panel>
      {pending ? <ConfirmationCard item={pending} onDone={async () => { setPending(null); await qc.invalidateQueries({ queryKey: ['memory'] }); await qc.invalidateQueries({ queryKey: ['pending-actions'] }) }} /> : null}
    </div>
  </Workspace>
}
