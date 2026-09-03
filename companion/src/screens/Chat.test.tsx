import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Chat } from './Chat'

function ownerSurfaceResponse(path: string) {
  if (path === '/api/work') return Response.json({ work: [] })
  if (path === '/api/cadence') return Response.json({ cadences: [] })
  if (path === '/api/health') return Response.json({ ok: true, service: 'atlas-api', version: '3.0.0' })
  return null
}

const conversation = {
  conversation_id: 'conversation_1',
  title: 'Chat',
  created_at: '2026-08-29 17:00:00',
  updated_at: '2026-08-29 17:34:11',
}

function renderChat() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const ownerSurface = ownerSurfaceResponse(path)
    if (ownerSurface) return ownerSurface
    if (path === '/api/chat/conversations' && (!init?.method || init.method === 'GET')) return Response.json({ conversations: [conversation] })
    if (path === '/api/actions/pending') return Response.json({ actions: [] })
    if (path === '/api/chat/conversations/conversation_1' && (!init?.method || init.method === 'GET')) return Response.json({ conversation, turns: [
      { turn_id: 'u1', conversation_id: 'conversation_1', role: 'user', content: 'Hello Atlas', metadata: {} },
      { turn_id: 'a1', conversation_id: 'conversation_1', role: 'assistant', content: 'Hello there', metadata: { tools_used: ['knowledge.search'] } },
    ] })
    if (path === '/api/chat/conversations/conversation_1' && init?.method === 'DELETE') return Response.json({ ok: true })
    if (path === '/api/chat/conversations/conversation_1/messages' && init?.method === 'POST') return Response.json({ turn: { turn_id: 'a2', conversation_id: 'conversation_1', role: 'assistant', content: 'Done', metadata: {} } })
    throw new Error(`unexpected fetch ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('confirm', vi.fn(() => true))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><Chat /></MemoryRouter></QueryClientProvider>)
  return fetchMock
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Chat', () => {
  it('renders a compact transcript and highlights the selected conversation', async () => {
    renderChat()
    expect(await screen.findByText('Hello there')).toBeInTheDocument()
    const selected = screen.getByRole('button', { name: /Chat 2026-08-29 17:34:11/ })
    expect(selected).toHaveClass('active')
    expect(screen.getByText('Hello there').closest('.chat-turn')).toHaveClass('assistant')
    expect(screen.getByText('Hello there').closest('.card')).toBeNull()
    expect(screen.getAllByText('knowledge.search')).not.toHaveLength(0)
  })

  it('filters the local conversation rail without changing the active thread', async () => {
    renderChat()
    expect(await screen.findByText('Hello there')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: 'Search conversations' }), { target: { value: 'missing' } })
    expect(screen.getByText('No matching conversations')).toBeInTheDocument()
    expect(screen.getByText('Hello there')).toBeInTheDocument()
  })

  it('deletes a conversation through the authenticated API surface', async () => {
    const fetchMock = renderChat()
    await screen.findByText('Hello there')
    fireEvent.click(screen.getByRole('button', { name: 'Delete Chat' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/chat/conversations/conversation_1', expect.objectContaining({ method: 'DELETE' })))
  })

  it('sends with Enter and keeps Shift+Enter for a new line', async () => {
    const fetchMock = renderChat()
    await screen.findByText('Hello there')
    const box = screen.getByRole('textbox', { name: 'Message' })
    fireEvent.change(box, { target: { value: 'First line' } })
    fireEvent.keyDown(box, { key: 'Enter', code: 'Enter', shiftKey: true })
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/messages'))).toBe(false)
    fireEvent.keyDown(box, { key: 'Enter', code: 'Enter' })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/chat/conversations/conversation_1/messages', expect.objectContaining({ method: 'POST' })))
  })
})

it('never sends to a conversation deleted from stale cache', async () => {
  const replacement = { ...conversation, conversation_id: 'conversation_2', title: 'Conversation', created_at: '2026-08-29 18:00:00', updated_at: '2026-08-29 18:00:00' }
  let list = [conversation]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const ownerSurface = ownerSurfaceResponse(path)
    if (ownerSurface) return ownerSurface
    if (path === '/api/chat/conversations' && (!init?.method || init.method === 'GET')) return Response.json({ conversations: list })
    if (path === '/api/actions/pending') return Response.json({ actions: [] })
    if (path === '/api/chat/conversations/conversation_1' && (!init?.method || init.method === 'GET')) return Response.json({ conversation, turns: [{ turn_id: 'a1', conversation_id: 'conversation_1', role: 'assistant', content: 'Old reply', metadata: {} }] })
    if (path === '/api/chat/conversations/conversation_1' && init?.method === 'DELETE') { list = []; return Response.json({ ok: true }) }
    if (path === '/api/chat/conversations' && init?.method === 'POST') { list = [replacement]; return Response.json(replacement) }
    if (path === '/api/chat/conversations/conversation_2' && (!init?.method || init.method === 'GET')) return Response.json({ conversation: replacement, turns: [] })
    if (path === '/api/chat/conversations/conversation_2/messages' && init?.method === 'POST') return Response.json({ turn: { turn_id: 'a2', conversation_id: 'conversation_2', role: 'assistant', content: 'New reply', metadata: {} } })
    throw new Error(`unexpected fetch ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('confirm', vi.fn(() => true))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><Chat /></MemoryRouter></QueryClientProvider>)
  await screen.findByText('Old reply')
  fireEvent.click(screen.getByRole('button', { name: 'Delete Chat' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/chat/conversations', expect.objectContaining({ method: 'POST' })))
  const box = screen.getByPlaceholderText('Ask Atlas…')
  fireEvent.change(box, { target: { value: 'Hi Atlas' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/chat/conversations/conversation_2/messages', expect.objectContaining({ method: 'POST' })))
  expect(fetchMock.mock.calls.some(([input, init]) => String(input) === '/api/chat/conversations/conversation_1/messages' && (init as RequestInit | undefined)?.method === 'POST')).toBe(false)
})


it('does not resurrect a stale embedded confirmation after the runtime has resolved it', async () => {
  const pending = {
    occurrence_id: 'action-restart', capability_id: 'host.service.restart', operation: 'restart', scope: 'host/service/atlas-api.service',
    payload_sha256: 'a'.repeat(64), policy_decision: 'CONFIRM', policy_revision: 1, status: 'pending_confirmation', created_at: 'now',
  }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const ownerSurface = ownerSurfaceResponse(path)
    if (ownerSurface) return ownerSurface
    if (path === '/api/chat/conversations' && (!init?.method || init.method === 'GET')) return Response.json({ conversations: [conversation] })
    if (path === '/api/actions/pending') return Response.json({ actions: [] })
    if (path === '/api/chat/conversations/conversation_1') return Response.json({ conversation, turns: [
      { turn_id: 'a1', conversation_id: 'conversation_1', role: 'assistant', content: 'Restart user service atlas-api.service', metadata: { requires_confirmation: true, action: pending } },
    ] })
    throw new Error(`unexpected fetch ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><Chat /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText('Restart user service atlas-api.service')).toBeInTheDocument()
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input) === '/api/actions/pending')).toBe(true))
  expect(screen.queryByText('Confirm exact action')).not.toBeInTheDocument()
})

it('anchors a live confirmation to the assistant turn that recorded it', async () => {
  const pending = {
    occurrence_id: 'action-restart', capability_id: 'host.service.restart', operation: 'restart', scope: 'host/service/atlas-api.service',
    payload_sha256: 'a'.repeat(64), policy_decision: 'CONFIRM', policy_revision: 1, status: 'pending_confirmation', created_at: 'now',
  }
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    const ownerSurface = ownerSurfaceResponse(path)
    if (ownerSurface) return ownerSurface
    if (path === '/api/chat/conversations') return Response.json({ conversations: [conversation] })
    if (path === '/api/actions/pending') return Response.json({ actions: [pending] })
    if (path === '/api/chat/conversations/conversation_1') return Response.json({ conversation, turns: [
      { turn_id: 'u1', conversation_id: 'conversation_1', role: 'user', content: 'Restart Atlas', metadata: {} },
      { turn_id: 'a1', conversation_id: 'conversation_1', role: 'assistant', content: 'Confirmation is required.', metadata: { requires_confirmation: true, action: pending } },
    ] })
    throw new Error(`unexpected fetch ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><Chat /></MemoryRouter></QueryClientProvider>)
  const confirm = await screen.findByRole('button', { name: 'Approve exact action' })
  expect(confirm.closest('.chat-turn')).toHaveTextContent('Confirmation is required.')
  expect(screen.getAllByText('host.service.restart')).not.toHaveLength(0)
})
