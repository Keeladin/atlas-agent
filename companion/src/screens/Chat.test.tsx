import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { renderScreen } from '../test/render'
import { Chat } from './Chat'

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: vi.fn() }
})

const mockedApi = vi.mocked(api)

describe('Chat handoff link', () => {
  beforeEach(() => {
    mockedApi.mockReset()
    mockedApi.mockImplementation(async (path) => {
      if (path === '/api/chat/conversations') {
        return { conversations: [{ id: 'chat-1', title: 'Report', turn_count: 3 }] }
      }
      if (path === '/api/chat/conversations/chat-1') {
        return {
          id: 'chat-1',
          title: 'Report',
          turn_count: 3,
          turns: [
            { id: 'turn-1', role: 'user', content: 'Draft the report', created_at: 'now' },
            { id: 'turn-2', role: 'assistant', content: 'Ready', created_at: 'now' },
            { id: 'turn-3', role: 'user', content: 'Send this as work', created_at: 'now' },
          ],
        }
      }
      throw new Error(`Unexpected API call: ${path}`)
    })
  })

  it('hands off the active conversation through the last substantive user turn', async () => {
    renderScreen(<Chat />, '/chat')

    await waitFor(() => {
      const links = screen.getAllByRole('link', { name: /Review in Work|Send to Work/ })
      for (const link of links) {
        expect(link).toHaveAttribute(
          'href',
          '/work/new?conversation=chat-1&until=turn-1',
        )
      }
    })
  })
})
