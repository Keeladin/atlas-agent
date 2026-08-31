import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { Attention } from './Attention'
import { AtlasMark } from './AtlasMark'

const NAV = [
  ['/chat', 'Chat'],
  ['/operations', 'Operations'],
  ['/atlas', 'Atlas'],
] as const

type InstallPromptEvent = Event & {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

function InstallAtlas() {
  const [prompt, setPrompt] = useState<InstallPromptEvent | null>(null)

  useEffect(() => {
    const capturePrompt = (event: Event) => {
      event.preventDefault()
      setPrompt(event as InstallPromptEvent)
    }
    window.addEventListener('beforeinstallprompt', capturePrompt)
    return () => window.removeEventListener('beforeinstallprompt', capturePrompt)
  }, [])

  if (!prompt) return null

  return <button type="button" className="install-app" onClick={() => {
    void prompt.prompt().then(() => prompt.userChoice).finally(() => setPrompt(null))
  }}>Install Atlas</button>
}

export function Shell({ onLogout }: { onLogout: () => void }) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><AtlasMark /><strong>Atlas</strong></div>
        <div className="topbar-actions"><InstallAtlas /><Attention /><button type="button" className="signout" onClick={onLogout}>Sign out</button></div>
      </header>
      <main className="main"><div className="main-scroll"><Outlet /></div></main>
      <nav className="nav-rail" aria-label="Primary">
        <div className="nav-rail-line" aria-hidden />
        <div className="nav-rail-tabs">
          {NAV.map(([to, label]) => <NavLink key={to} to={to} className="nav-tab">{label}</NavLink>)}
        </div>
      </nav>
    </div>
  )
}
