import { NavLink, Outlet } from 'react-router-dom'
import { Attention } from './Attention'
import { AtlasMark } from './AtlasMark'

const NAV = [
  ['/chat', 'Chat'],
  ['/work', 'Work'],
  ['/sources', 'Sources'],
  ['/atlas', 'Atlas'],
] as const

export function Shell({ onLogout }: { onLogout: () => void }) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><AtlasMark /><strong>Atlas</strong></div>
        <div className="topbar-actions"><Attention /><button type="button" className="signout" onClick={onLogout}>Sign out</button></div>
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
