import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionOccurrence, SourceRoot, WorkItem } from '../api/types'
import { Panel } from '../ui/Panel'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace } from '../ui/Workspace'
import { OPERATIONS_TABS } from './operationsNav'

type SourceRef = { root_id: string; relative_path: string; display_locator: string }
type SourceObservation = { source_ref: SourceRef; observed_at: string; object_type: string; byte_size?: number | null; consistency?: string; completeness?: string; metadata?: Record<string, unknown> }
type Listing = { observation: SourceObservation; entries: SourceObservation[]; next_cursor?: string | null; entry_errors?: Array<Record<string, unknown>> }
type ArtifactFacet = { root_id?: string | null; relative_path?: string | null; state?: string; byte_sha256?: string | null }
type Artifact = { artifact_id: string; display_name: string; media_type?: string | null; provenance?: Record<string, unknown>; facets: ArtifactFacet[]; managed_content?: Record<string, unknown>; managed_representations?: Array<Record<string, unknown>>; source_occurrences?: Array<Record<string, unknown>> }

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
function lifecycleLabel(artifact?: Artifact) {
  if (!artifact) return 'observed'
  if (artifact.managed_representations?.length) return 'managed'
  return 'established'
}

export function Sources() {
  const qc = useQueryClient()
  const roots = useQuery({ queryKey: ['source-roots'], queryFn: () => api<{ roots: SourceRoot[] }>('/api/sources/roots') })
  const artifacts = useQuery({ queryKey: ['artifacts'], queryFn: () => api<{ artifacts: Artifact[] }>('/api/artifacts') })
  const work = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work') })
  const [rootId, setRootId] = useState(''); const [path, setPath] = useState('.')
  const [listing, setListing] = useState<Listing | null>(null); const [selected, setSelected] = useState<SourceObservation | null>(null)
  const browse = useMutation({
    mutationFn: ({ root, relative, cursor }: { root: string; relative: string; cursor?: string | null }) => api<{ action: ActionOccurrence }>('/api/capabilities/files.list/invoke', { method: 'POST', body: JSON.stringify({ input: { root_id: root, relative_path: relative, page_size: 100, cursor: cursor ?? null } }) }),
    onSuccess: ({ action }, variables) => { if (action.status === 'succeeded' && action.result && typeof action.result === 'object') { setPath(variables.relative); setListing(action.result as Listing); setSelected(null) } },
  })
  const intake = useMutation({
    mutationFn: (entry: SourceObservation) => api<{ action: ActionOccurrence }>(
      '/api/capabilities/artifacts.intake_file/invoke',
      { method: 'POST', body: JSON.stringify({ input: { root_id: entry.source_ref.root_id, relative_path: entry.source_ref.relative_path } }) },
    ),
    onSuccess: ({ action }) => {
      if (action.status !== 'succeeded') throw new Error(action.error || `Intake ${action.status}.`)
    },
    onSettled: async () => {
      // Deterministic custody can succeed even when later semantic routing fails.
      // Always refresh runtime truth so the lifecycle reflects partial progress.
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['artifacts'] }),
        qc.invalidateQueries({ queryKey: ['work'] }),
      ])
    },
  })
  const enabledRoots = useMemo(() => (roots.data?.roots ?? []).filter(root => root.enabled), [roots.data?.roots])
  useEffect(() => { if (!rootId && enabledRoots.length) setRootId(enabledRoots[0].root_id) }, [enabledRoots, rootId])

  const artifactRows = useMemo(() => artifacts.data?.artifacts ?? [], [artifacts.data?.artifacts])
  const sourceArtifacts = useMemo(() => {
    const map = new Map<string, Artifact>()
    for (const artifact of artifactRows) for (const facet of artifact.facets ?? []) {
      if (facet.root_id && facet.relative_path) map.set(`${facet.root_id}:${facet.relative_path}`, artifact)
    }
    return map
  }, [artifactRows])
  function open(relative: string) { if (rootId) browse.mutate({ root: rootId, relative }) }
  const action = browse.data?.action
  const selectedArtifact = selected ? sourceArtifacts.get(`${selected.source_ref.root_id}:${selected.source_ref.relative_path}`) : undefined
  const managedId = selectedArtifact?.managed_representations?.[0]?.managed_artifact_id as string | undefined
  const managedArtifact = managedId ? artifactRows.find(item => item.artifact_id === managedId) : undefined
  const linkedWork = selectedArtifact ? (work.data?.work ?? []).filter(item => item.metadata?.source_artifact_id === selectedArtifact.artifact_id || item.metadata?.artifact_id === managedId) : []

  return <Workspace title="Sources" subtitle="External origins, managed custody, and the runtime state created from them." tabs={<SegmentedNav items={OPERATIONS_TABS} />}>
    <Panel title="Source intake">
      {!enabledRoots.length && !roots.isLoading ? <div className="empty-state"><strong>No source roots enrolled</strong><span>Add an allowed filesystem root under Atlas → Filesystem.</span></div> : null}
      {enabledRoots.length ? <div className="source-toolbar">
        <label>Source<select value={rootId} onChange={e => { setRootId(e.target.value); setPath('.'); setListing(null); setSelected(null) }}>{enabledRoots.map(root => <option key={root.root_id} value={root.root_id}>{root.display_name}</option>)}</select></label>
        <div className="source-path"><span className="eyebrow">Path</span><strong className="mono">{path}</strong></div>
        <div className="actions"><button type="button" disabled={path === '.' || browse.isPending} onClick={() => open(parentOf(path))}>Up</button><button className="primary" type="button" disabled={!rootId || browse.isPending} onClick={() => open(path)}>{browse.isPending ? 'Loading…' : listing ? 'Refresh' : 'Browse'}</button></div>
      </div> : null}
      {browse.isError ? <p className="offline-banner">{browse.error.message}</p> : null}
      {action && action.status !== 'succeeded' ? <p className="offline-banner">{action.error || `Listing ${action.status}.`}</p> : null}
      {listing ? <div className="source-browser">
        <div className="source-browser-head"><span>Name</span><span>Type</span><span>Size</span><span>Lifecycle</span></div>
        {!listing.entries.length ? <div className="empty-state compact"><strong>This folder is empty</strong></div> : null}
        {listing.entries.map(entry => {
          const isDir = entry.object_type === 'directory'
          const artifact = sourceArtifacts.get(`${entry.source_ref.root_id}:${entry.source_ref.relative_path}`)
          const lifecycle = lifecycleLabel(artifact)
          return <button type="button" className={`source-entry ${selected?.source_ref.relative_path === entry.source_ref.relative_path ? 'selected' : ''}`} key={`${entry.source_ref.relative_path}:${entry.observed_at}`} onClick={() => { if (isDir) open(entry.source_ref.relative_path); else { intake.reset(); setSelected(entry) } }} disabled={browse.isPending}>
            <span className="source-entry-name"><span aria-hidden>{isDir ? '▸' : '·'}</span><strong>{nameOf(entry)}</strong><small className="mono">{entry.source_ref.relative_path}</small></span>
            <span>{entry.object_type.replaceAll('_', ' ')}</span><span>{isDir ? '—' : formatBytes(entry.byte_size)}</span><span className={`chip ${lifecycle === 'managed' ? 'done' : ''}`}>{isDir ? 'browse' : lifecycle}</span>
          </button>
        })}
        {listing.next_cursor ? <button type="button" onClick={() => browse.mutate({ root: rootId, relative: path, cursor: listing.next_cursor })}>Next page</button> : null}
      </div> : enabledRoots.length ? <div className="empty-state compact"><strong>Select Browse to inspect this source</strong><span>Atlas will list only the enrolled root through the governed filesystem capability.</span></div> : null}
    </Panel>
    {selected ? <Panel title="Artifact lifecycle">
      <div className="artifact-lifecycle-head">
        <div><span className="eyebrow">Source file</span><strong>{nameOf(selected)}</strong><small className="mono">{selected.source_ref.relative_path}</small></div>
        <div className="actions">
          <span className={`chip ${managedArtifact ? 'done' : ''}`}>{managedArtifact ? 'managed' : selectedArtifact ? 'established' : 'observed'}</span>
          <button className="primary" type="button" disabled={intake.isPending || Boolean(linkedWork.length)} onClick={() => intake.mutate(selected)}>
            {intake.isPending ? 'Processing…' : linkedWork.length ? 'Intake complete' : managedArtifact ? 'Retry routing' : 'Start intake'}
          </button>
        </div>
      </div>
      {intake.isError ? <p className="offline-banner">{intake.error.message}</p> : null}
      {(() => {
        const preflight = intake.data?.action.result && typeof intake.data.action.result === 'object'
          ? (intake.data.action.result as Record<string, unknown>).workflow_preflight as Record<string, unknown> | undefined
          : undefined
        return preflight?.ok === false ? <p className="offline-banner">Workflow not started: {String(preflight.reason ?? 'deterministic preflight failed')}</p> : null
      })()}
      <div className="artifact-lifecycle-flow">
        <div className="lifecycle-station done"><span>Source</span><strong>Observed</strong><small>{formatBytes(selected.byte_size)}</small></div>
        <div className="lifecycle-arrow">→</div>
        <div className={`lifecycle-station ${selectedArtifact ? 'done' : ''}`}><span>Artifact</span><strong>{selectedArtifact ? 'Established' : 'Pending'}</strong><small>{selectedArtifact?.artifact_id ?? 'No durable identity yet'}</small></div>
        <div className="lifecycle-arrow">→</div>
        <div className={`lifecycle-station ${managedArtifact ? 'done' : ''}`}><span>Custody</span><strong>{managedArtifact ? 'Managed' : 'Not acquired'}</strong><small>{String(managedArtifact?.managed_content?.storage_name ?? 'Awaiting intake')}</small></div>
        <div className="lifecycle-arrow">→</div>
        <div className={`lifecycle-station ${linkedWork.length ? 'done' : ''}`}><span>Responsibility</span><strong>{linkedWork.length ? `${linkedWork.length} Work item${linkedWork.length === 1 ? '' : 's'}` : 'None yet'}</strong><small>{linkedWork[0]?.display_ref ?? 'Semantic routing has not created Work'}</small></div>
      </div>
      {linkedWork.length ? <div className="linked-work-list">{linkedWork.map(item => <Link key={item.work_id} to={`/work/${item.work_id}`} className="operations-row"><div><span className="eyebrow">{item.display_ref ?? 'Work'}</span><strong>{item.objective}</strong></div><span className="chip">{item.status.replaceAll('_', ' ')}</span></Link>)}</div> : null}
      <details className="inspect source-evidence"><summary>Technical evidence</summary><pre className="mono">{JSON.stringify({ observation: selected, source_artifact: selectedArtifact ?? null, managed_artifact: managedArtifact ?? null }, null, 2)}</pre></details>
    </Panel> : listing ? <div className="empty-state compact"><strong>Select a file to see its lifecycle</strong><span>Directories navigate the source. Files open their governed Artifact and Work state.</span></div> : null}
  </Workspace>
}
