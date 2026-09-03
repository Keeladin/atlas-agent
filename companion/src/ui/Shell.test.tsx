import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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
  it('keeps one owner surface and tucks plumbing behind Control', async () => {
    renderShell()
    expect(screen.queryByRole('navigation', { name: 'Primary' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Atlas surface' })).toHaveAttribute('href', '/chat')
    expect(screen.getByRole('link', { name: 'Control' })).toHaveAttribute('href', '/atlas')
    expect(screen.queryByText('Operations')).not.toBeInTheDocument()
    expect(screen.queryByText('Morning')).not.toBeInTheDocument()
    expect(await screen.findByText('Chat body')).toBeInTheDocument()
  })

  it('surfaces the generic runtime confirmation affordance', async () => {
    renderShell()
    expect(await screen.findByRole('button', { name: /Needs you/i })).toBeInTheDocument()
  })

  it('mounts the Needs You sheet outside shell stacking contexts', async () => {
    renderShell()
    const trigger = await screen.findByRole('button', { name: /Needs you/i })
    fireEvent.click(trigger)
    const dialog = await screen.findByRole('dialog')
    expect(dialog.parentElement).toBe(document.body)
  })
})
