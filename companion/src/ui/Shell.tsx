import { useQuery } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Attention } from './Attention'
import { AtlasMark } from './AtlasMark'

type InstallPromptEvent = Event & {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

type NavIcon = 'home' | 'work' | 'explorer' | 'library' | 'control'

function ShellIcon({ name }: { name: NavIcon }) {
  const common = { width: 20, height: 20, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true }
  if (name === 'home') return <svg {...common}><path d="M3.5 10.5 12 3l8.5 7.5" /><path d="M5.5 9.5v10h13v-10" /><path d="M9.5 19.5v-6h5v6" /></svg>
  if (name === 'work') return <svg {...common}><rect x="3.5" y="7" width="17" height="12.5" rx="2" /><path d="M8.5 7V4.5h7V7M3.5 11.5h17M10 11.5v2h4v-2" /></svg>
  if (name === 'explorer') return <svg {...common}><path d="M3.5 7.5h6l1.8 2h9.2v9.5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5z" /><path d="M3.5 7.5V5.5A1.5 1.5 0 0 1 5 4h4.2l1.8 2h8A1.5 1.5 0 0 1 20.5 7.5" /></svg>
  if (name === 'library') return <svg {...common}><path d="M5 4.5h5.5A1.5 1.5 0 0 1 12 6v14a2.5 2.5 0 0 0-2.5-2.5H5z" /><path d="M19 4.5h-5.5A1.5 1.5 0 0 0 12 6v14a2.5 2.5 0 0 1 2.5-2.5H19z" /></svg>
  return <svg {...common}><circle cx="12" cy="12" r="3" /><path d="M12 2.8v2.1M12 19.1v2.1M2.8 12h2.1M19.1 12h2.1M5.5 5.5 7 7M17 17l1.5 1.5M18.5 5.5 17 7M7 17l-1.5 1.5" /></svg>
}

function InstallAtlas() {
  const [prompt, setPrompt] = useState<InstallPromptEvent | null>(null)
  useEffect(() => {
    const capturePrompt = (event: Event) => { event.preventDefault(); setPrompt(event as InstallPromptEvent) }
    window.addEventListener('beforeinstallprompt', capturePrompt)
    return () => window.removeEventListener('beforeinstallprompt', capturePrompt)
  }, [])
  if (!prompt) return null
  return <button type="button" className="install-app" onClick={() => { void prompt.prompt().then(() => prompt.userChoice).finally(() => setPrompt(null)) }}>Install</button>
}

function QuickCommand() {
  const navigate = useNavigate()
  const [value, setValue] = useState('')
  function submit(event: FormEvent) {
    event.preventDefault()
    const query = value.trim()
    navigate(query ? `/chat?ask=${encodeURIComponent(query)}` : '/chat')
    setValue('')
  }
  return <form className="shell-command" onSubmit={submit}><span aria-hidden>✦</span><input aria-label="Quick command" value={value} onChange={event => setValue(event.target.value)} placeholder="Quick command…" /><kbd>⌘ K</kbd></form>
}

const BUILD_REVISION = import.meta.env.VITE_ATLAS_BUILD_SHA || 'unknown'
const shortRevision = (value?: string | null) => value && value !== 'unknown' ? value.replace(/-dirty$/, '').slice(0, 12) + (value.endsWith('-dirty') ? '-dirty' : '') : 'unknown'

const NAV_ITEMS: Array<{ to: string; label: string; icon: NavIcon; end?: boolean }> = [
  { to: '/', label: 'Home', icon: 'home', end: true },
  { to: '/work', label: 'Work', icon: 'work' },
  { to: '/sources', label: 'Explorer', icon: 'explorer' },
  { to: '/memory', label: 'Library', icon: 'library' },
  { to: '/atlas', label: 'Control', icon: 'control' },
]

export function Shell({ onLogout }: { onLogout: () => void }) {
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<{ ok: boolean; version: string; runtime_revision?: string }>('/api/health'), refetchInterval: 20000 })
  const runtimeRevision = health.data?.runtime_revision || health.data?.version
  const revisionMismatch = Boolean(runtimeRevision && BUILD_REVISION !== 'unknown' && runtimeRevision !== BUILD_REVISION)
  return <div className="app-shell atlas-shell-v4">
    <header className="topbar">
      <Link className="brand" to="/" aria-label="Atlas surface"><AtlasMark /><strong>Atlas</strong><span className="brand-subtitle">owner surface</span></Link>
      <QuickCommand />
      <div className="topbar-actions">{revisionMismatch ? <Link className="runtime-revision-mismatch" to="/atlas" title={`Companion ${BUILD_REVISION} · runtime ${runtimeRevision}`}>Build {shortRevision(BUILD_REVISION)} ≠ runtime {shortRevision(runtimeRevision)}</Link> : null}<span className={`shell-runtime ${health.data?.ok ? 'ready' : health.isError ? 'failed' : ''}`}><i />{health.data?.ok ? 'Runtime Ready' : health.isError ? 'Runtime Offline' : 'Checking'}{runtimeRevision ? <b>{shortRevision(runtimeRevision)}</b> : null}</span><InstallAtlas /><Attention /><Link className="topbar-control" to="/atlas">Control Center</Link><button type="button" className="signout" onClick={onLogout}>Sign out</button></div>
    </header>
    <div className="shell-body">
      <nav className="primary-rail" aria-label="Primary">{NAV_ITEMS.map(item => <NavLink key={item.to} to={item.to} end={item.end} aria-label={item.label}><ShellIcon name={item.icon} /><span>{item.label}</span></NavLink>)}</nav>
      <main className="main"><div className="main-scroll"><Outlet /></div></main>
    </div>
  </div>
}
