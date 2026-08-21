import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { loadSession, logout } from './api/client'
import { Chat } from './screens/Chat'
import { Home } from './screens/Home'
import { Login } from './screens/Login'
import { Files, Knowledge, Settings } from './screens/Placeholders'
import { WorkDetail } from './screens/WorkDetail'
import { WorkList } from './screens/WorkList'
import { WorkNew } from './screens/WorkNew'
import { Shell } from './ui/Shell'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

export default function App() {
  const [ready, setReady] = useState(false)
  const [authed, setAuthed] = useState(false)

  useEffect(() => {
    void loadSession()
      .then((session) => setAuthed(Boolean(session.authenticated)))
      .catch(() => setAuthed(false))
      .finally(() => setReady(true))
  }, [])

  if (!ready) {
    return <div className="empty" style={{ padding: '2rem' }}>Loading Atlas…</div>
  }

  if (!authed) {
    return <Login onAuthed={() => setAuthed(true)} />
  }

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route
            element={
              <Shell
                onLogout={() => {
                  void logout().then(() => setAuthed(false))
                }}
              />
            }
          >
            <Route path="/" element={<Home />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/work" element={<WorkList />} />
            <Route path="/work/new" element={<WorkNew />} />
            <Route path="/work/:workId" element={<WorkDetail />} />
            <Route path="/knowledge" element={<Knowledge />} />
            <Route path="/files" element={<Files />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
