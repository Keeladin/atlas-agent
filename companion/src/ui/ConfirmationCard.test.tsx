import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActionOccurrence } from '../api/types'
import { ConfirmationCard } from './ConfirmationCard'

const item: ActionOccurrence = {
  occurrence_id: 'action-1',
  capability_id: 'host.service.restart',
  operation: 'restart',
  scope: 'host/service/atlas-api.service',
  payload_sha256: 'a'.repeat(64),
  policy_decision: 'CONFIRM',
  policy_revision: 7,
  status: 'pending_confirmation',
  summary: 'Restart user service atlas-api.service',
  created_at: '2026-08-29T10:00:00Z',
}

function renderCard(onDone = vi.fn(async () => undefined)) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ConfirmationCard item={item} onDone={onDone} />
    </QueryClientProvider>,
  )
  return onDone
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
describe('ConfirmationCard', () => {
  it('shows the exact governed operation, resource, and payload hash', () => {
    renderCard()
    expect(screen.getByText('restart')).toBeInTheDocument()
    expect(screen.getByText('host/service/atlas-api.service')).toBeInTheDocument()
    expect(screen.getByText('a'.repeat(64))).toBeInTheDocument()
  })

  it('states the exact application-level purge guarantee and its boundary', () => {
    const memoryPurge = {
      ...item,
      capability_id: 'memory.purge',
      operation: 'purge',
      scope: 'atlas/memory/memory_ab12',
      summary: 'Purge memory memory_ab12',
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ConfirmationCard item={memoryPurge} onDone={async () => undefined} /></QueryClientProvider>)
    expect(screen.getByText(/Guaranteed\. After memory\.purge succeeds, within atlas-work\.db, in one transaction or not at all:/)).toBeInTheDocument()
    expect(screen.getByText(/Not guaranteed\. The originating chat turns in atlas-chat\.db/)).toBeInTheDocument()
    expect(screen.getByText('Purge is application-level suppression plus content redaction. It is not forensic erasure of the storage medium.')).toBeInTheDocument()
  })

  it('confirms the durable occurrence through the canonical endpoint', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ action: { ...item, status: 'succeeded' } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    const onDone = renderCard()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(fetchMock.mock.calls[0][0]).toBe('/api/actions/action-1/confirm')
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe('POST')
    await waitFor(() => expect(onDone).toHaveBeenCalled())
  })

  it('does not offer a second confirmation after a self-restart is dispatched', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ action: { ...item, status: 'uncertain' } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderCard(vi.fn(async () => { throw new Error('restart in progress') }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(await screen.findByText('Action dispatched. Waiting for runtime verification…')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
    expect(screen.getByText('uncertain')).toBeInTheDocument()
  })

  it('surfaces confirmation failures instead of failing silently', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ error: 'csrf token missing or invalid' }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderCard()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(await screen.findByText('csrf token missing or invalid')).toBeInTheDocument()
  })
})
