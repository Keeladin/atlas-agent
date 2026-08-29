import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Chat } from './Chat'

const conversation = {
  conversation_id: 'conversation_1',
  title: 'Chat',
  created_at: '2026-08-29 17:00:00',
  updated_at: '2026-08-29 17:34:11',
}

function renderChat() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path === '/api/chat/conversations' && (!init?.method || init.method === 'GET')) return Response.json({ conversations: [conversation] })
    if (path === '/api/chat/conversations/conversation_1' && (!init?.method || init.method === 'GET')) return Response.json({ conversation, turns: [
      { turn_id: 'u1', conversation_id: 'conversation_1', role: 'user', content: 'Hello Atlas', metadata: {} },
      { turn_id: 'a1', conversation_id: 'conversation_1', role: 'assistant', content: 'Hello there', metadata: {} },
    ] })
    if (path === '/api/chat/conversations/conversation_1' && init?.method === 'DELETE') return Response.json({ ok: true })
    throw new Error(`unexpected fetch ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('confirm', vi.fn(() => true))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><Chat /></QueryClientProvider>)
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
  })

  it('deletes a conversation through the authenticated API surface', async () => {
    const fetchMock = renderChat()
    await screen.findByText('Hello there')
    fireEvent.click(screen.getByRole('button', { name: 'Delete Chat' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/chat/conversations/conversation_1', expect.objectContaining({ method: 'DELETE' })))
  })
})

it('never sends to a conversation deleted from stale cache', async () => {
  const replacement = { ...conversation, conversation_id: 'conversation_2', title: 'Conversation', created_at: '2026-08-29 18:00:00', updated_at: '2026-08-29 18:00:00' }
  let list = [conversation]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path === '/api/chat/conversations' && (!init?.method || init.method === 'GET')) return Response.json({ conversations: list })
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
  render(<QueryClientProvider client={client}><Chat /></QueryClientProvider>)
  await screen.findByText('Old reply')
  fireEvent.click(screen.getByRole('button', { name: 'Delete Chat' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/chat/conversations', expect.objectContaining({ method: 'POST' })))
  const box = screen.getByPlaceholderText('Ask Atlas…')
  fireEvent.change(box, { target: { value: 'Hi Atlas' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/chat/conversations/conversation_2/messages', expect.objectContaining({ method: 'POST' })))
  expect(fetchMock.mock.calls.some(([input, init]) => String(input) === '/api/chat/conversations/conversation_1/messages' && (init as RequestInit | undefined)?.method === 'POST')).toBe(false)
})
