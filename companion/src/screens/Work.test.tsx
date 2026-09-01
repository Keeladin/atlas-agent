import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WorkList } from './Work'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Work queue', () => {
  it('shows deterministic step counts and selected responsibility truth', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const body = String(input).includes('/api/actions/pending') ? { actions: [] } : { work: [
        { work_id: 'w1', display_ref: 'DOC-1', objective: 'Review contract', status: 'active', owner_principal_id: 'owner-1', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z', metadata: {}, steps: [
          { step_id: 's1', ordinal: 1, description: 'Observe source', capability_id: 'files.read', input: {}, status: 'completed' },
          { step_id: 's2', ordinal: 2, description: 'Prepare review', capability_id: 'knowledge.search', input: {}, status: 'active' },
        ] },
      ] }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter><WorkList /></MemoryRouter></QueryClientProvider>)
    expect((await screen.findAllByText('Review contract')).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(screen.getByText('1 of 2 completed')).toBeInTheDocument()
    expect(screen.getByText('owner-1')).toBeInTheDocument()
  })
})
