import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api } from '../api/client'
import type { ActionOccurrence, MemoryItem } from '../api/types'
import { Panel } from '../ui/Panel'
import { Workspace } from '../ui/Workspace'

export function Memory() {
  const qc = useQueryClient()
  const [query, setQuery] = useState('')
  const memories = useQuery({
    queryKey: ['memory', query],
    queryFn: () => api<{ items: MemoryItem[] }>(`/api/memory${query ? `?q=${encodeURIComponent(query)}` : ''}`),
  })
  const act = useMutation({
    mutationFn: ({ itemId, action }: { itemId: string; action: 'retract' | 'restore' | 'purge' }) =>
      api<{ action: ActionOccurrence }>(`/api/memory/${itemId}/${action}`, { method: 'POST', body: '{}' }),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ['memory'] }) },
  })
  const deleteSource = useMutation({
    mutationFn: (conversationId: string) => api(`/api/chat/conversations/${conversationId}`, { method: 'DELETE' }),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ['conversations'] }) },
  })
  const items = useMemo(() => memories.data?.items ?? [], [memories.data])

  return <Workspace title="Memory" subtitle="Durable owner memory governed by the same live NO / YES principal policy as every consequential action.">
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
              <button className="danger" type="button" onClick={() => { if (window.confirm('Purge this memory chain? This is destructive and cannot be restored.')) act.mutate({ itemId: item.item_id, action: 'purge' }) }}>Purge</button>
              {sourceConversation ? <button type="button" onClick={() => { if (window.confirm('Delete the source conversation too? Memory purge cannot atomically remove chat turns from atlas-chat.db.')) deleteSource.mutate(sourceConversation) }}>Delete source conversation</button> : null}
            </div>
          </article>
        })}</div>
      </Panel>
    </div>
  </Workspace>
}
