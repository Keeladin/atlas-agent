import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { renderScreen } from '../test/render'
import { WorkNew } from './WorkNew'

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: vi.fn() }
})

const mockedApi = vi.mocked(api)

describe('Chat to Work handoff', () => {
  beforeEach(() => mockedApi.mockReset())

  it('compiles the bounded conversation into a reviewable plan without running it', async () => {
    mockedApi.mockResolvedValue({
      objective: 'Prepare the report',
      capabilities: ['generation.compose'],
      required_authority: 'read',
      expected_effect: 'A report draft',
      constraints: [],
    })

    renderScreen(
      <WorkNew />,
      '/work/new?conversation=conversation-1&until=turn-7',
    )

    expect(await screen.findByText('Prepare the report')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Accept Work' })).toBeEnabled()
    expect(mockedApi).toHaveBeenCalledOnce()
    expect(mockedApi).toHaveBeenCalledWith('/api/advanced/brief', {
      method: 'POST',
      body: JSON.stringify({
        conversation_id: 'conversation-1',
        until_turn_id: 'turn-7',
      }),
    })
    await waitFor(() => expect(mockedApi).not.toHaveBeenCalledWith('/api/work', expect.anything()))
  })
})
