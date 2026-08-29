import { useState, type FormEvent } from 'react'
import { login } from '../api/client'
import { AtlasMark } from '../ui/AtlasMark'

export function Login({ onAuthed }: { onAuthed: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { await login(password); onAuthed() } catch (exc) { setError(exc instanceof Error ? exc.message : 'Login failed') } finally { setBusy(false) }
  }
  return <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: '2rem' }}>
    <form className="card" style={{ width: 'min(420px, 100%)' }} onSubmit={submit}>
      <div className="brand" style={{ marginBottom: '1.5rem' }}><AtlasMark /><strong>Atlas</strong></div>
      <h2>Owner access</h2>
      <input autoFocus type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Companion password" />
      {error ? <p className="offline-banner">{error}</p> : null}
      <button className="primary" type="submit" disabled={busy || !password} style={{ width: '100%', marginTop: '1rem' }}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form>
  </div>
}
