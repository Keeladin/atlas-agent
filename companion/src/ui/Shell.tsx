import { NavLink, Outlet } from 'react-router-dom'

const NAV = [
  ['/', 'Home'],
  ['/chat', 'Chat'],
  ['/work', 'Work'],
  ['/knowledge', 'Knowledge'],
  ['/files', 'Files'],
  ['/settings', 'Settings'],
] as const

export function Shell({
  onLogout,
}: {
  onLogout: () => void
}) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(0, 1fr)',
        minHeight: '100vh',
      }}
    >
      <header
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 20,
          backdropFilter: 'blur(10px)',
          background: 'rgba(11, 18, 32, 0.9)',
          borderBottom: '1px solid var(--border)',
          padding: '0.75rem 1rem',
          display: 'flex',
          gap: '0.75rem',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <strong style={{ letterSpacing: '0.04em' }}>Atlas</strong>
          <nav
            style={{
              display: 'flex',
              gap: '0.35rem',
              flexWrap: 'wrap',
            }}
          >
            {NAV.map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                style={({ isActive }) => ({
                  padding: '0.55rem 0.8rem',
                  borderRadius: 999,
                  minHeight: 'var(--touch)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  background: isActive ? 'rgba(91, 140, 255, 0.2)' : 'transparent',
                  color: isActive ? 'white' : 'var(--text-muted)',
                  border: isActive
                    ? '1px solid rgba(91, 140, 255, 0.5)'
                    : '1px solid transparent',
                })}
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
        <button type="button" onClick={onLogout}>
          Sign out
        </button>
      </header>
      <main style={{ padding: '1rem', maxWidth: 1100, width: '100%', margin: '0 auto' }}>
        <Outlet />
      </main>
    </div>
  )
}
