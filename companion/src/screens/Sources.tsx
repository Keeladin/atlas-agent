import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { ActionOccurrence, SourceRoot } from '../api/types'
import { Panel } from '../ui/Panel'
import { Workspace } from '../ui/Workspace'

type SourceRef = { root_id: string; relative_path: string; display_locator: string }
type SourceObservation = { source_ref: SourceRef; observed_at: string; object_type: string; byte_size?: number | null; consistency?: string; completeness?: string; metadata?: Record<string, unknown> }
type Listing = { observation: SourceObservation; entries: SourceObservation[]; next_cursor?: string | null; entry_errors?: Array<Record<string, unknown>> }

function formatBytes(value?: number | null) {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']; let amount = value / 1024; let unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1 }
  return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
}

function nameOf(entry: SourceObservation) {
  const path = entry.source_ref.relative_path
  return path === '.' ? entry.source_ref.display_locator : path.split('/').filter(Boolean).at(-1) ?? path
}

function parentOf(path: string) {
  if (!path || path === '.') return '.'
  const parts = path.split('/').filter(Boolean); parts.pop(); return parts.length ? parts.join('/') : '.'
}

export function Sources() {
  const roots = useQuery({ queryKey: ['source-roots'], queryFn: () => api<{ roots: SourceRoot[] }>('/api/sources/roots') })
  const [rootId, setRootId] = useState(''); const [path, setPath] = useState('.'); const [knowledgeQ, setKnowledgeQ] = useState('')
  const [listing, setListing] = useState<Listing | null>(null)
  const browse = useMutation({
    mutationFn: ({ root, relative, cursor }: { root: string; relative: string; cursor?: string | null }) => api<{ action: ActionOccurrence }>('/api/capabilities/files.list/invoke', { method: 'POST', body: JSON.stringify({ input: { root_id: root, relative_path: relative, page_size: 100, cursor: cursor ?? null } }) }),
    onSuccess: ({ action }, variables) => { if (action.status === 'succeeded' && action.result && typeof action.result === 'object') { setPath(variables.relative); setListing(action.result as Listing) } },
  })
  const knowledge = useQuery({ queryKey: ['knowledge', knowledgeQ], queryFn: () => api<{ items: Array<Record<string, unknown>> }>(`/api/knowledge${knowledgeQ ? `?q=${encodeURIComponent(knowledgeQ)}` : ''}`) })
  const enabledRoots = useMemo(() => (roots.data?.roots ?? []).filter(root => root.enabled), [roots.data?.roots])
  useEffect(() => { if (!rootId && enabledRoots.length) setRootId(enabledRoots[0].root_id) }, [enabledRoots, rootId])

  function open(relative: string) { if (rootId) browse.mutate({ root: rootId, relative }) }
  const action = browse.data?.action
  return <Workspace title="Sources" subtitle="Browse enrolled sources and durable Atlas knowledge. Configuration and authority remain under Atlas.">
    <div className="sources-layout">
      <Panel title="Local sources">
        {!enabledRoots.length && !roots.isLoading ? <div className="empty-state"><strong>No source roots enrolled</strong><span>Add an allowed filesystem root under Atlas → Filesystem.</span></div> : null}
        {enabledRoots.length ? <div className="source-toolbar">
          <label>Source<select value={rootId} onChange={e => { setRootId(e.target.value); setPath('.'); setListing(null) }}>{enabledRoots.map(root => <option key={root.root_id} value={root.root_id}>{root.display_name}</option>)}</select></label>
          <div className="source-path"><span className="eyebrow">Path</span><strong className="mono">{path}</strong></div>
          <div className="actions"><button type="button" disabled={path === '.' || browse.isPending} onClick={() => open(parentOf(path))}>Up</button><button className="primary" type="button" disabled={!rootId || browse.isPending} onClick={() => open(path)}>{browse.isPending ? 'Loading…' : listing ? 'Refresh' : 'Browse'}</button></div>
        </div> : null}
        {browse.isError ? <p className="offline-banner">{browse.error.message}</p> : null}
        {action && action.status !== 'succeeded' ? <p className="offline-banner">{action.error || `Listing ${action.status}.`}</p> : null}
        {listing ? <div className="source-browser">
          <div className="source-browser-head"><span>Name</span><span>Type</span><span>Size</span><span>State</span></div>
          {!listing.entries.length ? <div className="empty-state compact"><strong>This folder is empty</strong></div> : null}
          {listing.entries.map(entry => { const isDir = entry.object_type === 'directory'; return <button type="button" className="source-entry" key={`${entry.source_ref.relative_path}:${entry.observed_at}`} onClick={() => isDir && open(entry.source_ref.relative_path)} disabled={!isDir || browse.isPending}>
            <span className="source-entry-name"><span aria-hidden>{isDir ? '▸' : '·'}</span><strong>{nameOf(entry)}</strong><small className="mono">{entry.source_ref.relative_path}</small></span>
            <span>{entry.object_type.replaceAll('_', ' ')}</span><span>{isDir ? '—' : formatBytes(entry.byte_size)}</span><span>{entry.consistency ?? '—'}</span>
          </button> })}
          {listing.next_cursor ? <button type="button" onClick={() => browse.mutate({ root: rootId, relative: path, cursor: listing.next_cursor })}>Next page</button> : null}
          <details className="inspect source-evidence"><summary>Observation evidence</summary><pre className="mono">{JSON.stringify({ observation: listing.observation, entry_errors: listing.entry_errors ?? [] }, null, 2)}</pre></details>
        </div> : enabledRoots.length ? <div className="empty-state compact"><strong>Select Browse to inspect this source</strong><span>Atlas will list only the enrolled root through the governed filesystem capability.</span></div> : null}
      </Panel>

      <Panel title="Knowledge">
        <div className="row-head"><span className="meta">Durable references and notes.</span><a className="button-link" href="/memory">Open Memory</a></div>
        <div className="knowledge-toolbar"><input value={knowledgeQ} onChange={e => setKnowledgeQ(e.target.value)} placeholder="Search durable knowledge" /><span className="meta">{knowledge.data?.items.length ?? 0} items</span></div>
        {knowledge.isError ? <p className="offline-banner">{knowledge.error.message}</p> : null}
        {!knowledge.isLoading && !(knowledge.data?.items.length) ? <div className="empty-state compact"><strong>No matching durable context</strong><span>{knowledgeQ ? 'Try a broader search.' : 'Knowledge captured by Atlas will appear here.'}</span></div> : null}
        <div className="knowledge-list">{(knowledge.data?.items ?? []).map((item, index) => <article className="knowledge-item" key={String(item.item_id ?? index)}><div className="row-head"><strong>{String(item.title ?? 'Untitled')}</strong><span className="chip">{String(item.kind ?? 'item')}</span></div><p>{String(item.content ?? '').slice(0, 800)}</p></article>)}</div>
      </Panel>
    </div>
  </Workspace>
}
