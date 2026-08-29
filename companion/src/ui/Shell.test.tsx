import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Shell } from './Shell'

function renderShell() {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ actions: [] }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/chat']}>
        <Routes>
          <Route element={<Shell onLogout={() => undefined} />}>
            <Route path="/chat" element={<div>Chat body</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Atlas shell', () => {
  it('keeps the primary destinations in runtime order with Atlas last', async () => {
    renderShell()
    const nav = screen.getByRole('navigation', { name: 'Primary' })
    const labels = Array.from(nav.querySelectorAll('a')).map(node => node.textContent)
    expect(labels).toEqual(['Chat', 'Work', 'Sources', 'Atlas'])
    expect(screen.queryByText('Now')).not.toBeInTheDocument()
    expect(screen.queryByText('Morning')).not.toBeInTheDocument()
    expect(await screen.findByText('Chat body')).toBeInTheDocument()
  })

  it('surfaces the generic runtime confirmation affordance', async () => {
    renderShell()
    expect(await screen.findByRole('button', { name: /Needs you/i })).toBeInTheDocument()
  })
})
