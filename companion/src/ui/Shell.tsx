import { useEffect, useState } from 'react'
import { Link, Outlet } from 'react-router-dom'
import { Attention } from './Attention'
import { AtlasMark } from './AtlasMark'

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
  }}>Install</button>
}

export function Shell({ onLogout }: { onLogout: () => void }) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/chat" aria-label="Atlas surface"><AtlasMark /><strong>Atlas</strong><span className="brand-subtitle">owner surface</span></Link>
        <div className="topbar-actions"><InstallAtlas /><Attention /><Link className="topbar-control" to="/atlas">Control</Link><button type="button" className="signout" onClick={onLogout}>Sign out</button></div>
      </header>
      <main className="main"><div className="main-scroll"><Outlet /></div></main>
    </div>
  )
}
