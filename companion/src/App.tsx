import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AUTH_EXPIRED_EVENT, loadSession, logout } from './api/client'
import { Atlas } from './screens/Atlas'
import { CadenceList } from './screens/Cadence'
import { Chat } from './screens/Chat'
import { Login } from './screens/Login'
import { Sources } from './screens/Sources'
import { WorkList } from './screens/Work'
import { WorkDetail } from './screens/WorkDetail'
import { Shell } from './ui/Shell'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })

export default function App() {
  const [ready, setReady] = useState(false)
  const [authed, setAuthed] = useState(false)
  useEffect(() => { void loadSession().then(session => setAuthed(Boolean(session.authenticated))).catch(() => setAuthed(false)).finally(() => setReady(true)) }, [])
  useEffect(() => { const expired = () => { queryClient.clear(); setAuthed(false) }; window.addEventListener(AUTH_EXPIRED_EVENT, expired); return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expired) }, [])
  if (!ready) return <div className="empty" style={{ padding: '2rem' }}>Loading Atlas…</div>
  if (!authed) return <Login onAuthed={() => setAuthed(true)} />
  return <QueryClientProvider client={queryClient}><BrowserRouter><Routes><Route element={<Shell onLogout={() => { void logout().then(() => { queryClient.clear(); setAuthed(false) }) }} />}><Route path="/" element={<Navigate to="/chat" replace />} /><Route path="/chat" element={<Chat />} /><Route path="/work" element={<WorkList />} /><Route path="/work/:workId" element={<WorkDetail />} /><Route path="/cadence" element={<CadenceList />} /><Route path="/sources" element={<Sources />} /><Route path="/atlas" element={<Atlas />} /><Route path="*" element={<Navigate to="/chat" replace />} /></Route></Routes></BrowserRouter></QueryClientProvider>
}
