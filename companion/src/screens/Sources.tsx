import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionOccurrence, SourceRoot, WorkItem } from '../api/types'
import { SegmentedNav } from '../ui/SegmentedNav'
import {
  FactList,
  InspectorPanel,
  InspectorSection,
  OperationalRibbon,
  OperationalRow,
  StatusLamp,
} from '../ui/OperationsPrimitives'
import { reviewStateToLamp, runtimeStateToLamp, workStateToLamp } from '../ui/operationState'
import { Workspace, WorkspaceRailSection } from '../ui/Workspace'
import { OPERATIONS_TABS } from './operationsNav'

type SourceRef = { root_id: string; relative_path: string; display_locator: string }
type SourceObservation = { source_ref: SourceRef; observed_at: string; object_type: string; byte_size?: number | null; consistency?: string; completeness?: string; metadata?: Record<string, unknown> }
type Listing = { observation: SourceObservation; entries: SourceObservation[]; next_cursor?: string | null; entry_errors?: Array<Record<string, unknown>> }
type ArtifactFacet = { root_id?: string | null; relative_path?: string | null; state?: string; byte_sha256?: string | null }
type Artifact = { artifact_id: string; display_name: string; media_type?: string | null; provenance?: Record<string, unknown>; facets: ArtifactFacet[]; managed_content?: Record<string, unknown>; managed_representations?: Array<Record<string, unknown>>; source_occurrences?: Array<Record<string, unknown>> }
type LibraryScan = { scan_id: string; status: string; source_roots: string[]; summary: Record<string, number>; error?: string | null; created_at: string; completed_at?: string | null }
type LibraryReview = { root_id: string; relative_path: string; status: 'reviewed' | 'approved' | 'rejected'; updated_at: string }
type SourceMode = 'browse' | 'consolidate'
type EntryFilter = 'all' | 'files' | 'folders' | 'unreviewed' | 'reviewed' | 'approved' | 'rejected'

function formatBytes(value?: number | null) {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']; let amount = value / 1024; let unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1 }
  return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
}

function nameOf(entry: SourceObservation) {
  const value = entry.source_ref.relative_path
  return value === '.' ? entry.source_ref.display_locator : value.split('/').filter(Boolean).at(-1) ?? value
}

function parentOf(value: string) {
  if (!value || value === '.') return '.'
  const parts = value.split('/').filter(Boolean); parts.pop(); return parts.length ? parts.join('/') : '.'
}

function lifecycleLabel(artifact?: Artifact) {
  if (!artifact) return 'observed'
  if (artifact.managed_representations?.length) return 'managed'
  return 'established'
}

function scanIdFromOutput(output: unknown): string | undefined {
  if (!output || typeof output !== 'object') return undefined
  const row = output as Record<string, unknown>
  if (typeof row.scan_id === 'string') return row.scan_id
  if (row.scan && typeof row.scan === 'object' && typeof (row.scan as Record<string, unknown>).scan_id === 'string') return String((row.scan as Record<string, unknown>).scan_id)
  return undefined
}

function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

export function Sources() {
  const qc = useQueryClient()
  const roots = useQuery({ queryKey: ['source-roots'], queryFn: () => api<{ roots: SourceRoot[] }>('/api/sources/roots') })
  const artifacts = useQuery({ queryKey: ['artifacts'], queryFn: () => api<{ artifacts: Artifact[] }>('/api/artifacts') })
  const work = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work') })
  const scans = useQuery({ queryKey: ['library-scans'], queryFn: () => api<{ scans: LibraryScan[] }>('/api/library/scans') })
  const reviews = useQuery({ queryKey: ['library-reviews'], queryFn: () => api<{ reviews: LibraryReview[] }>('/api/library/reviews') })
  const [mode, setMode] = useState<SourceMode>('browse')
  const [rootId, setRootId] = useState(''); const [path, setPath] = useState('.')
  const [listing, setListing] = useState<Listing | null>(null); const [selected, setSelected] = useState<SourceObservation | null>(null)
  const [scanRoots, setScanRoots] = useState<string[]>([])
  const [entryFilter, setEntryFilter] = useState<EntryFilter>('all')
  const [search, setSearch] = useState('')

  const browse = useMutation({
    mutationFn: ({ root, relative, cursor }: { root: string; relative: string; cursor?: string | null }) => api<{ action: ActionOccurrence }>('/api/capabilities/files.list/invoke', { method: 'POST', body: JSON.stringify({ input: { root_id: root, relative_path: relative, page_size: 100, cursor: cursor ?? null } }) }),
    onSuccess: ({ action }, variables) => {
      if (action.status === 'succeeded' && action.result && typeof action.result === 'object') {
        setPath(variables.relative); setListing(action.result as Listing); setSelected(null)
      }
    },
  })
  const intake = useMutation({
    mutationFn: (entry: SourceObservation) => api<{ action: ActionOccurrence }>('/api/capabilities/artifacts.intake_file/invoke', { method: 'POST', body: JSON.stringify({ input: { root_id: entry.source_ref.root_id, relative_path: entry.source_ref.relative_path } }) }),
    onSuccess: ({ action }) => { if (action.status !== 'succeeded') throw new Error(action.error || `Intake ${action.status}.`) },
    onSettled: async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['artifacts'] }), qc.invalidateQueries({ queryKey: ['work'] })]) },
  })
  const enabledRoots = useMemo(() => (roots.data?.roots ?? []).filter(root => root.enabled), [roots.data?.roots])
  const consolidationSources = useMemo(() => enabledRoots.filter(root => root.provider_namespace !== 'atlas-library'), [enabledRoots])
  const cleanLibraryRoot = useMemo(() => enabledRoots.find(root => root.provider_namespace === 'atlas-library'), [enabledRoots])
  useEffect(() => { if (!rootId && enabledRoots.length) setRootId((consolidationSources[0] ?? enabledRoots[0]).root_id) }, [consolidationSources, enabledRoots, rootId])
  useEffect(() => { if (!scanRoots.length && consolidationSources.length) setScanRoots(consolidationSources.map(root => root.root_id)) }, [consolidationSources, scanRoots.length])
  const scanLibrary = useMutation({
    mutationFn: () => api<{ action: ActionOccurrence; work?: WorkItem }>('/api/work', { method: 'POST', body: JSON.stringify({
      objective: 'Consolidate source library into one exact-copy-clean set',
      steps: [
        { capability_id: 'library.scan_duplicates', description: 'Recursively hash source files and group exact duplicates', input: { root_ids: scanRoots, max_files: 10000 } },
        { capability_id: 'library.materialize', description: 'Copy one canonical file per exact-content group into the clean library', input: { scan_id: { $ref: { step: 1, output: '/scan/scan_id' } }, destination_root_id: cleanLibraryRoot?.root_id, destination_relative_path: '.' } },
      ], run: true,
    }) }),
    onSuccess: async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['library-scans'] }), qc.invalidateQueries({ queryKey: ['work'] })]) },
  })
  const setReview = useMutation({
    mutationFn: ({ entry, status }: { entry: SourceObservation; status: 'unreviewed' | 'reviewed' | 'approved' | 'rejected' }) => api<{ action: ActionOccurrence }>('/api/capabilities/library.set_review/invoke', { method: 'POST', body: JSON.stringify({ input: { root_id: entry.source_ref.root_id, relative_path: entry.source_ref.relative_path, status } }) }),
    onSuccess: async ({ action }) => { if (action.status !== 'succeeded') throw new Error(action.error || `Review update ${action.status}.`); await qc.invalidateQueries({ queryKey: ['library-reviews'] }) },
  })

  const artifactRows = useMemo(() => artifacts.data?.artifacts ?? [], [artifacts.data?.artifacts])
  const sourceArtifacts = useMemo(() => {
    const map = new Map<string, Artifact>()
    for (const artifact of artifactRows) for (const facet of artifact.facets ?? []) if (facet.root_id && facet.relative_path) map.set(`${facet.root_id}:${facet.relative_path}`, artifact)
    return map
  }, [artifactRows])
  const reviewMap = useMemo(() => new Map((reviews.data?.reviews ?? []).map(item => [`${item.root_id}:${item.relative_path}`, item])), [reviews.data?.reviews])
  const selectedArtifact = selected ? sourceArtifacts.get(`${selected.source_ref.root_id}:${selected.source_ref.relative_path}`) : undefined
  const managedId = selectedArtifact?.managed_representations?.[0]?.managed_artifact_id as string | undefined
  const managedArtifact = managedId ? artifactRows.find(item => item.artifact_id === managedId) : undefined
  const linkedWork = selectedArtifact ? (work.data?.work ?? []).filter(item => item.metadata?.source_artifact_id === selectedArtifact.artifact_id || item.metadata?.artifact_id === managedId) : []
  const selectedReview = selected ? reviewMap.get(`${selected.source_ref.root_id}:${selected.source_ref.relative_path}`) : undefined
  const isCleanLibrary = rootId === cleanLibraryRoot?.root_id
  const latestScan = scans.data?.scans?.[0]
  const relatedWork = latestScan ? (work.data?.work ?? []).find(item => item.steps?.some(step => scanIdFromOutput(step.output) === latestScan.scan_id)) : undefined
  const managedCount = artifactRows.filter(item => item.managed_content).length
  const visibleEntries = useMemo(() => (listing?.entries ?? []).filter(entry => {
    const directory = entry.object_type === 'directory'
    const review = reviewMap.get(`${entry.source_ref.root_id}:${entry.source_ref.relative_path}`)?.status ?? 'unreviewed'
    if (search && !nameOf(entry).toLowerCase().includes(search.toLowerCase())) return false
    if (entryFilter === 'files' && directory) return false
    if (entryFilter === 'folders' && !directory) return false
    if (['unreviewed', 'reviewed', 'approved', 'rejected'].includes(entryFilter) && (directory || review !== entryFilter)) return false
    return true
  }), [entryFilter, listing?.entries, reviewMap, search])

  function selectRoot(id: string) { setRootId(id); setPath('.'); setListing(null); setSelected(null); setEntryFilter('all'); setSearch('') }
  function open(relative: string) { if (rootId) browse.mutate({ root: rootId, relative }) }

  const sourceRail = <div className="ops-rail-panel">
    <WorkspaceRailSection title={mode === 'browse' ? 'Enrolled sources' : 'Consolidation sources'}>
      {enabledRoots.map(root => mode === 'browse' ? <OperationalRow key={root.root_id} active={root.root_id === rootId} lamp={root.enabled ? 'green' : 'dim'} label={root.display_name} secondary={<span className="mono">{root.root_id}</span>} onClick={() => selectRoot(root.root_id)} /> : root.provider_namespace !== 'atlas-library' ? <label className="ops-check-row" aria-label={`Include ${root.display_name}`} key={root.root_id}><input type="checkbox" checked={scanRoots.includes(root.root_id)} onChange={event => setScanRoots(current => event.target.checked ? [...current, root.root_id] : current.filter(id => id !== root.root_id))} /><span><strong>{root.display_name}</strong><small className="mono">{root.root_id}</small></span></label> : null)}
    </WorkspaceRailSection>
    {mode === 'browse' ? <WorkspaceRailSection title="View">
      <div className="ops-filter-stack">{(['all', 'files', 'folders', ...(isCleanLibrary ? ['unreviewed', 'reviewed', 'approved', 'rejected'] : [])] as EntryFilter[]).map(filter => <button type="button" className={entryFilter === filter ? 'active' : ''} key={filter} onClick={() => setEntryFilter(filter)}>{filter.replaceAll('_', ' ')}</button>)}</div>
    </WorkspaceRailSection> : <WorkspaceRailSection title="Destination">
      <OperationalRow lamp={cleanLibraryRoot ? 'green' : 'red'} label={cleanLibraryRoot?.display_name ?? 'Atlas Clean Library unavailable'} secondary={cleanLibraryRoot ? <span className="mono">{cleanLibraryRoot.root_id}</span> : undefined} />
      <p className="meta">Consolidation creates exact copies. Source folders are never modified.</p>
    </WorkspaceRailSection>}
  </div>

  const fileInspector = selected ? <InspectorPanel title={nameOf(selected)} eyebrow="Selected file" status={<StatusLamp tone={runtimeStateToLamp(lifecycleLabel(selectedArtifact))} label={lifecycleLabel(selectedArtifact)} />} actions={<>
    <button type="button" onClick={() => window.open(`/api/sources/file?root_id=${encodeURIComponent(selected.source_ref.root_id)}&relative_path=${encodeURIComponent(selected.source_ref.relative_path)}`, '_blank', 'noopener,noreferrer')}>View file</button>
    {isCleanLibrary ? <button type="button" disabled={setReview.isPending} onClick={() => setReview.mutate({ entry: selected, status: 'reviewed' })}>Mark reviewed</button> : null}
    {isCleanLibrary ? <button className="confirm" type="button" disabled={setReview.isPending} onClick={() => setReview.mutate({ entry: selected, status: 'approved' })}>Approve</button> : null}
    {isCleanLibrary ? <button className="danger" type="button" disabled={setReview.isPending} onClick={() => setReview.mutate({ entry: selected, status: 'rejected' })}>Reject</button> : null}
    {isCleanLibrary && selectedReview ? <button type="button" disabled={setReview.isPending} onClick={() => setReview.mutate({ entry: selected, status: 'unreviewed' })}>Reset review</button> : null}
    <button className="primary" type="button" disabled={intake.isPending || Boolean(linkedWork.length) || (isCleanLibrary && selectedReview?.status !== 'approved')} onClick={() => intake.mutate(selected)}>{intake.isPending ? 'Processing…' : linkedWork.length ? 'Intake complete' : isCleanLibrary && selectedReview?.status !== 'approved' ? 'Approve before intake' : managedArtifact ? 'Retry routing' : 'Start intake'}</button>
  </>}>
    <InspectorSection title="File identity"><FactList items={[
      { label: 'Source', value: enabledRoots.find(root => root.root_id === selected.source_ref.root_id)?.display_name ?? selected.source_ref.root_id },
      { label: 'Relative path', value: selected.source_ref.relative_path, mono: true },
      { label: 'Type', value: selected.object_type.replaceAll('_', ' ') },
      { label: 'Size', value: formatBytes(selected.byte_size), mono: true },
    ]} /></InspectorSection>
    <InspectorSection title="Runtime lifecycle"><div className="ops-timeline compact">
      <div><StatusLamp tone="green" /><span><strong>Observed</strong><small>{when(selected.observed_at)}</small></span></div>
      <div><StatusLamp tone={selectedArtifact ? 'green' : 'dim'} /><span><strong>Established</strong><small className="mono">{selectedArtifact?.artifact_id ?? 'Not established'}</small></span></div>
      <div><StatusLamp tone={managedArtifact ? 'green' : 'dim'} /><span><strong>Managed</strong><small className="mono">{String(managedArtifact?.managed_content?.storage_name ?? 'Not managed')}</small></span></div>
    </div></InspectorSection>
    {isCleanLibrary ? <InspectorSection title="Human review"><StatusLamp tone={reviewStateToLamp(selectedReview?.status)} label={selectedReview?.status ?? 'unreviewed'} /></InspectorSection> : null}
    {linkedWork.length ? <InspectorSection title="Linked Work">{linkedWork.map(item => <Link className="ops-linked-row" to={`/work/${item.work_id}`} key={item.work_id}><span><strong>{item.display_ref ?? item.work_id}</strong><small>{item.objective}</small></span><StatusLamp tone={workStateToLamp(item.status)} label={item.status.replaceAll('_', ' ')} /></Link>)}</InspectorSection> : null}
    {setReview.isError ? <p className="offline-banner">Review update failed: {setReview.error.message}</p> : null}
    {intake.isError ? <p className="offline-banner">{intake.error.message}</p> : null}
    <details className="inspect source-evidence"><summary>Technical evidence</summary><pre className="mono">{JSON.stringify({ observation: selected, source_artifact: selectedArtifact ?? null, managed_artifact: managedArtifact ?? null }, null, 2)}</pre></details>
  </InspectorPanel> : <InspectorPanel title="No file selected" eyebrow="File inspector"><div className="empty-state compact"><strong>Select a file</strong><span>Its runtime lifecycle, review state, linked Work, and available actions will remain visible here.</span></div></InspectorPanel>

  const consolidationInspector = <InspectorPanel title={latestScan ? `Scan ${latestScan.scan_id}` : 'No consolidation scan'} eyebrow="Latest scan" status={<StatusLamp tone={runtimeStateToLamp(latestScan?.status)} label={latestScan?.status ?? 'not started'} />} actions={<>
    {cleanLibraryRoot ? <button type="button" onClick={() => { setMode('browse'); selectRoot(cleanLibraryRoot.root_id) }}>Browse Clean Library</button> : null}
    {relatedWork ? <Link className="button-link" to={`/work/${relatedWork.work_id}`}>View Work</Link> : null}
  </>}>
    {latestScan ? <>
      <InspectorSection title="Run"><FactList items={[{ label: 'Created', value: when(latestScan.created_at), mono: true }, { label: 'Completed', value: when(latestScan.completed_at), mono: true }, { label: 'Sources', value: latestScan.source_roots.length }, { label: 'Related Work', value: relatedWork?.display_ref ?? 'Not available', mono: true }]} /></InspectorSection>
      {latestScan.error ? <p className="offline-banner">{latestScan.error}</p> : null}
    </> : <div className="empty-state compact"><strong>No scan history</strong><span>Select sources and start consolidation to create ordinary governed Work.</span></div>}
  </InspectorPanel>

  return <Workspace className="operations-workspace" title="Sources" subtitle="External origins, managed custody, and the runtime state created from them." tabs={<><SegmentedNav items={OPERATIONS_TABS} /><div className="ops-mode-tabs" aria-label="Sources mode"><button type="button" className={mode === 'browse' ? 'active' : ''} onClick={() => setMode('browse')}>File browser</button><button type="button" className={mode === 'consolidate' ? 'active' : ''} onClick={() => setMode('consolidate')}>Consolidation</button></div></>} rail={sourceRail} railLabel={mode === 'browse' ? 'Sources and filters' : 'Consolidation sources'} context={mode === 'browse' ? fileInspector : consolidationInspector} contextLabel={mode === 'browse' ? 'File details' : 'Scan details'} banner={<OperationalRibbon items={mode === 'browse' ? [
    { label: 'Enrolled', value: enabledRoots.length, tone: enabledRoots.length ? 'green' : 'dim' },
    { label: 'Observed artifacts', value: artifactRows.length, tone: artifactRows.length ? 'green' : 'dim' },
    { label: 'Managed', value: managedCount, tone: managedCount ? 'green' : 'dim' },
    { label: 'Current source', value: enabledRoots.find(root => root.root_id === rootId)?.display_name ?? 'None', tone: rootId ? 'green' : 'dim' },
  ] : [
    { label: 'Selected roots', value: scanRoots.length, tone: scanRoots.length ? 'green' : 'dim' },
    { label: 'Latest status', value: latestScan?.status ?? 'Not started', tone: runtimeStateToLamp(latestScan?.status) },
    { label: 'Files scanned', value: latestScan?.summary.files_scanned ?? '—', tone: latestScan ? 'green' : 'dim' },
    { label: 'Duplicate copies', value: latestScan?.summary.duplicate_copies ?? '—', tone: latestScan ? 'amber' : 'dim' },
  ]} />}>
    {mode === 'browse' ? <section className="ops-surface source-workspace" aria-label="File browser">
      <header className="ops-surface-head">
        <div><span className="eyebrow">Current path</span><strong className="mono">{path}</strong></div>
        <div className="source-browser-tools"><input aria-label="Search current folder" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search current folder…" /><button type="button" disabled={path === '.' || browse.isPending} onClick={() => open(parentOf(path))}>Up</button><button className="primary" type="button" disabled={!rootId || browse.isPending} onClick={() => open(path)}>{!rootId ? 'Loading sources…' : browse.isPending ? 'Loading…' : listing ? 'Refresh' : 'Browse'}</button></div>
      </header>
      {browse.isError ? <p className="offline-banner">{browse.error.message}</p> : null}
      {browse.data?.action && browse.data.action.status !== 'succeeded' ? <p className="offline-banner">{browse.data.action.error || `Listing ${browse.data.action.status}.`}</p> : null}
      {listing ? <div className="source-table">
        <div className="source-table-head"><span>Name</span><span>Type</span><span>Size</span><span>{isCleanLibrary ? 'Review' : 'Lifecycle'}</span></div>
        <div className="source-table-body">{visibleEntries.map(entry => {
          const directory = entry.object_type === 'directory'
          const artifact = sourceArtifacts.get(`${entry.source_ref.root_id}:${entry.source_ref.relative_path}`)
          const review = reviewMap.get(`${entry.source_ref.root_id}:${entry.source_ref.relative_path}`)
          const state = isCleanLibrary ? (review?.status ?? 'unreviewed') : lifecycleLabel(artifact)
          return <button type="button" className={`source-table-row ${selected?.source_ref.relative_path === entry.source_ref.relative_path ? 'active' : ''}`} key={`${entry.source_ref.relative_path}:${entry.observed_at}`} onClick={() => directory ? open(entry.source_ref.relative_path) : (intake.reset(), setSelected(entry))} disabled={browse.isPending}>
            <span className="source-file-name"><span aria-hidden>{directory ? '▸' : '·'}</span><span><strong>{nameOf(entry)}</strong><small className="mono">{entry.source_ref.relative_path}</small></span></span>
            <span>{entry.object_type.replaceAll('_', ' ')}</span><span className="mono">{directory ? '—' : formatBytes(entry.byte_size)}</span><StatusLamp tone={isCleanLibrary ? reviewStateToLamp(review?.status) : runtimeStateToLamp(state)} label={directory ? 'browse' : state} />
          </button>
        })}</div>
        {!visibleEntries.length ? <div className="empty-state compact"><strong>No matching entries</strong><span>Change the current filter or browse another path.</span></div> : null}
        {listing.next_cursor ? <div className="ops-table-footer"><button type="button" onClick={() => browse.mutate({ root: rootId, relative: path, cursor: listing.next_cursor })}>Next page</button></div> : null}
      </div> : <div className="empty-state"><strong>Select Browse to inspect this source</strong><span>Atlas lists only the enrolled root through the governed filesystem capability.</span></div>}
    </section> : <section className="ops-surface consolidation-workspace" aria-label="Library consolidation">
      <header className="ops-surface-head"><div><span className="eyebrow">Exact-copy clean set</span><strong>Library consolidation</strong><p>Hash selected source roots, retain one canonical copy of each byte-distinct file, and leave every source unchanged.</p></div><button className="primary" type="button" disabled={!scanRoots.length || !cleanLibraryRoot || scanLibrary.isPending} onClick={() => scanLibrary.mutate()}>{scanLibrary.isPending ? 'Starting…' : 'Start consolidation'}</button></header>
      {scanLibrary.isError ? <p className="offline-banner">Scan failed: {scanLibrary.error.message}</p> : null}
      <div className="consolidation-results">{[
        ['Files scanned', latestScan?.summary.files_scanned], ['Unique files', latestScan?.summary.unique_files], ['Duplicate copies', latestScan?.summary.duplicate_copies], ['Duplicate groups', latestScan?.summary.duplicate_groups],
      ].map(([label, value]) => <div key={String(label)}><span>{label}</span><strong>{value ?? '—'}</strong></div>)}</div>
      <div className="scan-history"><h2>Scan history</h2>{(scans.data?.scans ?? []).map(scan => <OperationalRow key={scan.scan_id} lamp={runtimeStateToLamp(scan.status)} label={<span className="mono">{scan.scan_id}</span>} secondary={when(scan.created_at)} meta={`${scan.summary.files_scanned ?? 0} files`} status={<span className={`chip ${scan.status === 'completed' ? 'done' : scan.status === 'failed' ? 'failed' : 'running'}`}>{scan.status}</span>} />)}{!scans.data?.scans?.length ? <div className="empty-state compact"><strong>No scan history</strong></div> : null}</div>
    </section>}
  </Workspace>
}
