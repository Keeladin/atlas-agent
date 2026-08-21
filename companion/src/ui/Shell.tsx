import { NavLink, Outlet } from 'react-router-dom'

const NAV = [
  ['/', 'Home'],
  ['/chat', 'Chat'],
  ['/work', 'Work'],
  ['/knowledge', 'Knowledge'],
  ['/files', 'Files'],
] as const

const MORE = [['/settings', 'Settings']] as const

export function Shell({ onLogout }: { onLogout: () => void }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden />
          <div>
            <strong>Atlas</strong>
            <small>Personal workspace</small>
          </div>
        </div>
        <nav className="nav">
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === '/'}>
              <span className="nav-dot" />
              {label}
            </NavLink>
          ))}
          {MORE.map(([to, label]) => (
            <NavLink key={to} to={to}>
              <span className="nav-dot" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <strong>Signed in</strong>
          <small>Owner session</small>
          <div className="actions" style={{ marginTop: '0.65rem' }}>
            <button type="button" onClick={onLogout}>
              Sign out
            </button>
          </div>
        </div>
      </aside>

      <main className="main with-bottom-nav">
        <Outlet />
      </main>

      <nav className="bottom-nav" aria-label="Primary">
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === '/'}>
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
