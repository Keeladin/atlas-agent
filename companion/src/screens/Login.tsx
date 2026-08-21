import { useState } from 'react'
import type { FormEvent } from 'react'
import { login } from '../api/client'
import { Panel } from '../ui/Panel'

export function Login({ onAuthed }: { onAuthed: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(password)
      onAuthed()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        padding: '1rem',
      }}
    >
      <Panel title="Atlas Companion">
        <p style={{ color: 'var(--text-muted)', marginTop: 0 }}>
          Sign in to the Atlas host. Sessions use an HTTP-only cookie over HTTPS.
        </p>
        <form onSubmit={onSubmit} style={{ display: 'grid', gap: '0.75rem' }}>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error ? (
            <div style={{ color: 'var(--danger)' }}>{error}</div>
          ) : null}
          <button className="primary" type="submit" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </Panel>
    </div>
  )
}
