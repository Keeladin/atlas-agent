import { useMutation, useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { ActionOccurrence, SourceRoot } from '../api/types'
import { Panel } from '../ui/Panel'
import { Workspace } from '../ui/Workspace'

export function Sources() {
  const roots = useQuery({ queryKey: ['source-roots'], queryFn: () => api<{ roots: SourceRoot[] }>('/api/sources/roots') })
  const [rootId, setRootId] = useState(''); const [path, setPath] = useState('.'); const [knowledgeQ, setKnowledgeQ] = useState('')
  const browse = useMutation({ mutationFn: () => api<{ action: ActionOccurrence }>('/api/capabilities/files.list/invoke', { method: 'POST', body: JSON.stringify({ input: { root_id: rootId, relative_path: path } }) }) })
  const knowledge = useQuery({ queryKey: ['knowledge', knowledgeQ], queryFn: () => api<{ items: Array<Record<string, unknown>> }>(`/api/knowledge${knowledgeQ ? `?q=${encodeURIComponent(knowledgeQ)}` : ''}`) })
  function submit(event: FormEvent) { event.preventDefault(); browse.mutate() }
  return <Workspace title="Sources" subtitle="Browse enrolled local sources and durable Atlas knowledge. Configuration lives in Atlas.">
    <div className="grid-2">
      <Panel title="Local sources">
        <form className="stack" onSubmit={submit}>
          <label>Root<select value={rootId} onChange={e => setRootId(e.target.value)}><option value="">Select root</option>{(roots.data?.roots ?? []).filter(r => r.enabled).map(root => <option key={root.root_id} value={root.root_id}>{root.display_name}</option>)}</select></label>
          <label>Relative path<input value={path} onChange={e => setPath(e.target.value)} /></label>
          <button className="primary" type="submit" disabled={!rootId || browse.isPending}>List</button>
        </form>
        {browse.data ? <pre className="mono" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(browse.data.action.result ?? browse.data.action, null, 2)}</pre> : null}
        {browse.isError ? <p className="offline-banner">{browse.error.message}</p> : null}
      </Panel>
      <Panel title="Knowledge & memory">
        <input value={knowledgeQ} onChange={e => setKnowledgeQ(e.target.value)} placeholder="Search durable context" />
        <div className="stack" style={{ marginTop: '1rem' }}>{(knowledge.data?.items ?? []).map((item, index) => <div className="list-row" key={String(item.item_id ?? index)}><strong>{String(item.title ?? 'Item')}</strong><div className="meta">{String(item.kind ?? '')}</div><div>{String(item.content ?? '').slice(0, 600)}</div></div>)}</div>
      </Panel>
    </div>
  </Workspace>
}
