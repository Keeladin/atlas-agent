import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { AttentionItem, Cadence, Conversation, SourceRoot, WorkItem } from '../api/types'
import { StatusLamp } from '../ui/OperationsPrimitives'
import { attentionDetail, attentionHref, attentionStatus, attentionTitle } from '../ui/attentionState'

type Health = { ok: boolean; service: string; version: string }
type HostFilesystem = { path?: string; total?: number; used?: number; free?: number }
type HomeSystem = {
  version: string
  capabilities: Array<{ id: string; available: boolean }>
  host: {
    resources?: { load_1?: number; memory?: Record<string, string | null | undefined> }
    storage?: { filesystems?: HostFilesystem[] }
  }
}
type Artifact = {
  artifact_id: string
  display_name: string
  media_type?: string | null
  created_at: string
  facets?: Array<{ kind: string; state: string; root_id?: string | null; relative_path?: string | null }>
}

function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const diff = Date.now() - date.getTime()
  if (diff >= 0 && diff < 60_000) return 'just now'
  if (diff >= 0 && diff < 3_600_000) return `${Math.max(1, Math.floor(diff / 60_000))}m ago`
  if (diff >= 0 && diff < 86_400_000) return `${Math.max(1, Math.floor(diff / 3_600_000))}h ago`
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function greeting() {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

function formatBytes(value?: number) {
  if (value == null || !Number.isFinite(value)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let amount = value
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1 }
  return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
}

function parseKb(value?: string | null) {
  const match = String(value || '').match(/^([0-9.]+)\s*kB$/i)
  return match ? Number(match[1]) * 1024 : 0
}

function artifactKind(item?: Artifact | null) {
  const media = String(item?.media_type || '').toLowerCase()
  if (media.startsWith('image/')) return 'image'
  if (media === 'application/pdf') return 'pdf'
  if (media.includes('spreadsheet') || media.includes('csv')) return 'sheet'
  if (media.startsWith('text/') || media.includes('json') || media.includes('markdown')) return 'text'
  return 'file'
}

function ArtifactGlyph({ kind }: { kind: ReturnType<typeof artifactKind> }) {
  if (kind === 'image') return <span aria-hidden>▧</span>
  if (kind === 'pdf') return <span aria-hidden>▤</span>
  if (kind === 'sheet') return <span aria-hidden>▦</span>
  if (kind === 'text') return <span aria-hidden>≡</span>
  return <span aria-hidden>◇</span>
}

function ArtifactPreview({ artifact }: { artifact: Artifact }) {
  const kind = artifactKind(artifact)
  const readable = artifact.facets?.some(facet => facet.kind === 'local_file' && facet.state === 'present') ?? false
  const url = `/api/artifacts/${artifact.artifact_id}/content`
  if (readable && kind === 'image') return <img src={url} alt={artifact.display_name} />
  if (readable && kind === 'pdf') return <iframe title={artifact.display_name} src={url} />
  return <div className={`home-preview-placeholder ${kind}`}><ArtifactGlyph kind={kind} /><strong>{artifact.display_name}</strong><span>{artifact.media_type || 'Governed artifact'}</span></div>
}

export function Home() {
  const navigate = useNavigate()
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<Health>('/api/health'), refetchInterval: 15000 })
  const system = useQuery({ queryKey: ['system'], queryFn: () => api<HomeSystem>('/api/system'), refetchInterval: 15000 })
  const work = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work'), refetchInterval: 8000 })
  const cadence = useQuery({ queryKey: ['cadence'], queryFn: () => api<{ cadences: Cadence[] }>('/api/cadence'), refetchInterval: 15000 })
  const artifacts = useQuery({ queryKey: ['artifacts'], queryFn: () => api<{ artifacts: Artifact[] }>('/api/artifacts'), refetchInterval: 20000 })
  const conversations = useQuery({ queryKey: ['conversations'], queryFn: () => api<{ conversations: Conversation[] }>('/api/chat/conversations'), refetchInterval: 15000 })
  const roots = useQuery({ queryKey: ['source-roots'], queryFn: () => api<{ roots: SourceRoot[] }>('/api/sources/roots'), refetchInterval: 30000 })
  const attention = useQuery({ queryKey: ['attention'], queryFn: () => api<{ attention: AttentionItem[] }>('/api/attention'), refetchInterval: 5000 })
  const [selectedArtifactId, setSelectedArtifactId] = useState('')
  const [ask, setAsk] = useState('')

  const workRows = useMemo(() => work.data?.work ?? [], [work.data?.work])
  const artifactRows = useMemo(() => artifacts.data?.artifacts ?? [], [artifacts.data?.artifacts])
  const recentArtifacts = artifactRows.slice(0, 6)
  useEffect(() => {
    if (!selectedArtifactId && recentArtifacts.length) setSelectedArtifactId(recentArtifacts[0].artifact_id)
    else if (selectedArtifactId && !artifactRows.some(item => item.artifact_id === selectedArtifactId)) setSelectedArtifactId(recentArtifacts[0]?.artifact_id ?? '')
  }, [artifactRows, recentArtifacts, selectedArtifactId])

  const selectedArtifact = artifactRows.find(item => item.artifact_id === selectedArtifactId) ?? recentArtifacts[0]
  const attentionRows = attention.data?.attention ?? []
  const active = workRows.filter(item => ['active', 'running', 'queued'].includes(item.status))
  const latestWork = [...workRows].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0]
  const upcoming = (cadence.data?.cadences ?? []).filter(item => item.enabled && item.next_run_at).sort((a, b) => String(a.next_run_at).localeCompare(String(b.next_run_at)))[0]
  const latestConversation = conversations.data?.conversations?.[0]
  const filesystems = system.data?.host.storage?.filesystems ?? []
  const enabledRoots = (roots.data?.roots ?? []).filter(root => root.enabled).slice(0, 4)

  const matters = [
    attentionRows.length ? { tone: 'red', title: `${attentionRows.length} ${attentionRows.length === 1 ? 'commitment needs' : 'commitments need'} your attention`, detail: attentionTitle(attentionRows[0]), meta: attentionStatus(attentionRows[0]) }
      : { tone: 'green', title: 'Nothing needs your attention', detail: 'No open obligation currently requires owner attention.', meta: 'clear' },
    active.length ? { tone: 'blue', title: `${active.length} ${active.length === 1 ? 'responsibility is' : 'responsibilities are'} active`, detail: active[0].objective, meta: active[0].status }
      : { tone: 'dim', title: 'No Work is currently running', detail: 'Atlas has no active responsibility at this moment.', meta: 'quiet' },
    recentArtifacts.length ? { tone: 'violet', title: `${recentArtifacts.length} recent ${recentArtifacts.length === 1 ? 'artifact is' : 'artifacts are'} ready to inspect`, detail: recentArtifacts[0].display_name, meta: when(recentArtifacts[0].created_at) }
      : upcoming ? { tone: 'blue', title: 'A standing duty is coming up', detail: upcoming.name, meta: when(upcoming.next_run_at) }
        : { tone: 'dim', title: 'No new context is waiting', detail: 'Your visible Atlas world is quiet.', meta: 'quiet' },
  ] as const

  const attentionNext = attentionRows[0]
  const recommendedWork = active[0] ?? latestWork
  const recommendedHref = attentionNext ? attentionHref(attentionNext) : recommendedWork ? `/work/${recommendedWork.work_id}` : latestConversation ? '/chat' : '/chat?ask=What%20should%20we%20work%20on%20next%3F'
  const recommendedTitle = attentionNext ? attentionTitle(attentionNext) : recommendedWork ? recommendedWork.objective : latestConversation ? `Continue “${latestConversation.title}”` : 'Decide what Atlas should take on next'
  const recommendedDetail = attentionNext ? attentionDetail(attentionNext) : recommendedWork ? `${recommendedWork.status.replaceAll('_', ' ')} · updated ${when(recommendedWork.updated_at)}` : latestConversation ? `Last active ${when(latestConversation.updated_at)}` : 'No unfinished Work is currently visible.'
  const memTotal = parseKb(system.data?.host.resources?.memory?.MemTotal)
  const memAvailable = parseKb(system.data?.host.resources?.memory?.MemAvailable)
  const memUsed = memTotal ? Math.max(0, memTotal - memAvailable) : 0
  const memPercent = memTotal ? Math.round((memUsed / memTotal) * 100) : 0

  function submit(event: FormEvent) {
    event.preventDefault()
    const value = ask.trim()
    navigate(value ? `/chat?ask=${encodeURIComponent(value)}` : '/chat')
  }

  return <div className="atlas-home">
    <section className="home-briefing">
      <header className="home-greeting"><div><h1>{greeting()}, Jaco</h1><p>Here is what Atlas can verify right now.</p></div><span className={`home-health ${health.data?.ok ? 'ok' : health.isError ? 'bad' : ''}`}><StatusLamp tone={health.data?.ok ? 'green' : health.isError ? 'red' : 'dim'} />{health.data?.ok ? 'Systems healthy' : health.isError ? 'Runtime unavailable' : 'Checking runtime'}</span></header>

      <div className="home-section-label"><span>What matters now</span></div>
      <div className="home-matters">{matters.map((item, index) => <article key={item.title}>
        <span className={`matter-index tone-${item.tone}`}>{index + 1}</span>
        <div><strong>{item.title}</strong><small>{item.detail}</small></div>
        <span className={`matter-meta tone-${item.tone}`}>{item.meta}</span>
      </article>)}</div>

      <div className="home-section-label"><span>Recommended next</span></div>
      <Link className="home-recommended" to={recommendedHref}>
        <span className="home-spark">✦</span><span><strong>{recommendedTitle}</strong><small>{recommendedDetail}</small></span><span>Open →</span>
      </Link>

      <div className="home-conversation-area">
        <section className="home-conversations"><div className="home-section-label"><span>Recent conversations</span><Link to="/chat">View all</Link></div>{(conversations.data?.conversations ?? []).slice(0, 3).map((item, index) => <Link key={item.conversation_id} to={`/chat?conversation=${encodeURIComponent(item.conversation_id)}`} className={index === 0 ? 'active' : ''}><span className="conversation-glyph">◫</span><span><strong>{item.title}</strong><small>{when(item.updated_at)}</small></span></Link>)}{!conversations.data?.conversations?.length ? <p className="home-empty">No conversations yet.</p> : null}</section>
        <section className="home-chat-console"><div className="home-atlas-line"><span className="home-atlas-mark">◎</span><span><strong>Atlas</strong><small>{health.data?.ok ? 'Ready when you are.' : 'Runtime state is being checked.'}</small></span></div><form onSubmit={submit}><input value={ask} onChange={event => setAsk(event.target.value)} aria-label="Ask Atlas from Home" placeholder="Ask Atlas anything…" /><button type="submit" aria-label="Open conversation">↗</button></form></section>
      </div>
    </section>

    <section className="home-featured">
      <div className="home-section-label"><span>Featured context</span>{selectedArtifact ? <small>{artifactKind(selectedArtifact)}</small> : null}</div>
      {selectedArtifact ? <div className="featured-context"><header><div><strong>{selectedArtifact.display_name}</strong><small>{selectedArtifact.media_type || 'Governed artifact'} · {when(selectedArtifact.created_at)}</small></div><Link to={`/chat?ask=${encodeURIComponent(`About ${selectedArtifact.display_name}: `)}`}>Ask Atlas →</Link></header><div className="featured-preview"><ArtifactPreview artifact={selectedArtifact} /></div><footer><span>Selected from Atlas artifacts</span><a href={`/api/artifacts/${selectedArtifact.artifact_id}/content`} target="_blank" rel="noreferrer">Open source view ↗</a></footer></div>
        : <div className="featured-empty"><span>◇</span><strong>No renderable context yet</strong><p>Files, images, PDFs and other governed objects will appear here when Atlas has them.</p></div>}
    </section>

    <aside className="home-right-rail">
      <section className="home-explorer"><div className="home-section-label"><span>Explorer</span><Link to="/sources">Open</Link></div><div className="home-storage-list">{filesystems.slice(0, 4).map((fs, index) => { const total = fs.total ?? 0; const used = fs.used ?? Math.max(0, total - (fs.free ?? 0)); const pct = total ? Math.min(100, Math.round(used / total * 100)) : 0; return <div key={`${fs.path}:${index}`}><span className="storage-icon">▱</span><span><strong>{fs.path || 'Filesystem'}</strong><small>{formatBytes(fs.free)} free of {formatBytes(total)}</small></span><i><b style={{ width: `${pct}%` }} /></i></div>})}{!filesystems.length ? <p className="home-empty">No mounted storage telemetry.</p> : null}</div><div className="home-places">{enabledRoots.map(root => <Link key={root.root_id} to="/sources"><span>▰</span>{root.display_name}<b>›</b></Link>)}</div></section>

      <section className="home-recent-files"><div className="home-section-label"><span>Recent files</span><Link to="/sources">View all</Link></div><div className="recent-file-grid">{recentArtifacts.map(item => <button key={item.artifact_id} type="button" className={item.artifact_id === selectedArtifact?.artifact_id ? 'active' : ''} onClick={() => setSelectedArtifactId(item.artifact_id)}><span className={`file-thumb ${artifactKind(item)}`}>{artifactKind(item) === 'image' && item.facets?.some(facet => facet.kind === 'local_file' && facet.state === 'present') ? <img src={`/api/artifacts/${item.artifact_id}/content`} alt="" /> : <ArtifactGlyph kind={artifactKind(item)} />}</span><strong>{item.display_name}</strong><small>{artifactKind(item).toUpperCase()}</small></button>)}{!recentArtifacts.length ? <p className="home-empty">No recent artifacts.</p> : null}</div></section>

      <section className="home-runtime"><div className="home-section-label"><span>Runtime state</span><Link to="/atlas/runtime">Details</Link></div><div className="runtime-metrics"><div><span>Load</span><strong>{system.data?.host.resources?.load_1?.toFixed(2) ?? '—'}</strong><i /></div><div><span>Memory</span><strong>{memTotal ? `${memPercent}%` : '—'}</strong><i><b style={{ width: `${memPercent}%` }} /></i></div><div><span>Capabilities</span><strong>{system.data?.capabilities?.filter(item => item.available).length ?? '—'} ready</strong><i /></div></div><div className="runtime-orbit"><span className={health.data?.ok ? 'ready' : ''}>✓</span><div><strong>{health.data?.ok ? 'All systems operational' : 'Runtime status pending'}</strong><small>{health.data?.ok ? `Atlas ${health.data.version}` : 'Waiting for verified health state.'}</small></div></div></section>
    </aside>
  </div>
}
