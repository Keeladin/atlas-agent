import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Operations } from './Operations'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Operations overview', () => {
  it('summarizes actual runtime records without invented risk or productivity state', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      let body: object
      if (url.includes('/api/health')) body = { ok: true, service: 'atlas-api', version: '3.5.0' }
      else if (url.includes('/api/sources/roots')) body = { roots: [{ root_id: 'proof', provider_namespace: 'local', host_path: '/srv/proof', display_name: 'Proof', enabled: true, updated_at: 'now' }] }
      else if (url.includes('/api/artifacts')) body = { artifacts: [{ artifact_id: 'a1', display_name: 'Proof', managed_content: { storage_name: 'proof' } }] }
      else if (url.includes('/api/cadence')) body = { cadences: [] }
      else if (url.includes('/api/library/scans')) body = { scans: [] }
      else body = { work: [{ work_id: 'w1', display_ref: 'DOC-1', objective: 'Review contract', status: 'active', owner_principal_id: 'owner', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z', metadata: {}, steps: [{ step_id: 's1', ordinal: 1, description: 'Review', capability_id: 'knowledge.search', input: {}, status: 'active' }] }] }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter><Operations /></MemoryRouter></QueryClientProvider>)
    expect((await screen.findAllByText('Review contract')).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('atlas-api')).toBeInTheDocument()
    expect(screen.queryByText(/at risk/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/productivity/i)).not.toBeInTheDocument()
  })
})
