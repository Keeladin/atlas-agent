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
      setError(err instanceof Error ? err.message : 'Sign-in failed')
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
      <Panel>
        <div className="brand" style={{ marginBottom: '0.85rem' }}>
          <div className="brand-mark" aria-hidden />
          <div>
            <strong>Atlas</strong>
            <small>Personal workspace</small>
          </div>
        </div>
        <p className="meta" style={{ marginTop: 0, marginBottom: '0.9rem' }}>
          Sign in to your Atlas host. Sessions use a secure cookie over HTTPS.
        </p>
        <form onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>
          {error ? <p className="error-text">{error}</p> : null}
          <button className="primary" type="submit" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </Panel>
    </div>
  )
}
