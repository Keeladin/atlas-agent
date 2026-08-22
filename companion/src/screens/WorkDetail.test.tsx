import { act, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { api } from '../api/client'
import { renderScreen } from '../test/render'
import { workDetail } from '../test/workFixture'
import { WorkDetail } from './WorkDetail'

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: vi.fn() }
})

class FakeEventSource extends EventTarget {
  static instances: FakeEventSource[] = []
  readonly url: string
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close = vi.fn()

  constructor(url: string) {
    super()
    this.url = url
    FakeEventSource.instances.push(this)
  }
}

const mockedApi = vi.mocked(api)

function renderDetail() {
  return renderScreen(
    <Routes>
      <Route path="/work/:workId" element={<WorkDetail />} />
    </Routes>,
    '/work/work-1',
  )
}

describe('Work detail states and event stream', () => {
  beforeEach(() => {
    mockedApi.mockReset()
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  it('renders approval and payload confirmation decisions without a run control', async () => {
    mockedApi.mockResolvedValue(workDetail({
      phase: 'waiting_authority',
      blocking: { kind: 'authority', message: 'Your decision is required.' },
      actions: ['run'],
      pending_approvals: [{
        id: 'approval-1', work_id: 'work-1', step_id: null,
        required_authority: 'external', requested_action: 'Send the report',
        status: 'pending', created_at: 'now',
      }],
      pending_confirmations: [{
        id: 'confirmation-1', work_id: 'work-1', step_id: 'step-1',
        capability_id: 'communication.email.send', payload_sha256: 'hash',
        summary: 'Send to the customer', payload: { invocation_input: { to: 'customer@example.test' } },
        status: 'pending', created_at: 'now',
      }],
    }))

    renderDetail()

    expect(await screen.findByRole('button', { name: 'Approve authority' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Confirm payload' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /Start|Resume/ })).not.toBeInTheDocument()
    expect(screen.getByText('Decide in the centre first.', { exact: false })).toBeInTheDocument()
  })

  it.each([
    ['completed', "Done — here's what changed."],
    ['failed', "I couldn't finish this. Inspect activity and recover if needed."],
  ])('renders the %s outcome from runtime truth', async (status, outcome) => {
    mockedApi.mockResolvedValue(workDetail({ status, phase: 'terminal' }))
    renderDetail()
    expect(await screen.findByText(outcome)).toBeInTheDocument()
  })

  it('keeps one connection while events invalidate and refetch detail', async () => {
    mockedApi.mockResolvedValue(workDetail({ phase: 'waiting' }))
    renderDetail()
    await screen.findByText('Send the report')
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0]?.url).toBe('/api/work/work-1/events/stream?after=0')

    act(() => {
      FakeEventSource.instances[0]?.dispatchEvent(
        new MessageEvent('work.completed', { lastEventId: '12' }),
      )
    })
    await waitFor(() => expect(mockedApi.mock.calls.length).toBeGreaterThan(1))
    expect(FakeEventSource.instances).toHaveLength(1)

    vi.useFakeTimers()
    act(() => {
      FakeEventSource.instances[0]?.onerror?.(new Event('error'))
      vi.advanceTimersByTime(1000)
    })
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances[1]?.url).toBe('/api/work/work-1/events/stream?after=12')
    vi.useRealTimers()
  })
})
