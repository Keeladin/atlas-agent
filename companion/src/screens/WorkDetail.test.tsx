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
const emailStep = {
  id: 'step-1', ordinal: 1, description: 'Send the report',
  capability: 'communication.email.send', capability_version: '1',
  status: 'running', dependencies: [], input_artifact_ids: [],
}

function renderDetail() {
  return renderScreen(<Routes><Route path="/work/:workId" element={<WorkDetail />} /></Routes>, '/work/work-1')
}

describe('Work detail operator view', () => {
  beforeEach(() => {
    mockedApi.mockReset()
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  it('uses a running execution as the active capability source', async () => {
    mockedApi.mockResolvedValue(workDetail({
      steps: [emailStep],
      executions: [{
        id: 'execution-1', step_id: 'step-1', capability: 'communication.email.send',
        capability_version: '1', provider: 'smtp', attempt: 2, status: 'running',
        error: null, receipt: {}, started_at: '2026-01-01T12:00:00Z', ended_at: null,
        input_artifact_ids: [], output_artifact_ids: [],
      }],
    }))
    renderDetail()
    expect(await screen.findByText('Active capability')).toBeInTheDocument()
    expect(screen.getAllByText('Send an email').length).toBeGreaterThan(0)
    expect(screen.getByText(/Attempt 2/)).toBeInTheDocument()
  })

  it('falls back to a running step capability only when no execution is running', async () => {
    mockedApi.mockResolvedValue(workDetail({ steps: [emailStep] }))
    renderDetail()
    expect(await screen.findByText('Running step capability')).toBeInTheDocument()
    expect(screen.queryByText('Active execution')).not.toBeInTheDocument()
  })

  it('makes a pending payload confirmation visibly blocking and distinct from authority', async () => {
    mockedApi.mockResolvedValue(workDetail({
      phase: 'waiting_confirmation',
      blocking: { kind: 'payload_confirmation', message: 'Confirm the exact payload.' },
      actions: ['run'],
      pending_confirmations: [{
        id: 'confirmation-1', work_id: 'work-1', step_id: 'step-1',
        capability_id: 'communication.email.send', payload_sha256: 'hash',
        summary: 'Send the report to the customer',
        payload: { invocation_input: { to: 'customer@example.test' } }, status: 'pending', created_at: 'now',
      }],
    }))
    renderDetail()
    expect(await screen.findByText('Waiting for payload confirmation')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm payload' })).toBeEnabled()
    expect(screen.getByText(/does not grant a new authority scope/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Start|Resume/ })).not.toBeInTheDocument()
  })

  it('renders an authority request as distinct from payload confirmation', async () => {
    mockedApi.mockResolvedValue(workDetail({
      phase: 'waiting_authority',
      blocking: { kind: 'authority_approval', message: 'Authority is required.' },
      pending_approvals: [{
        id: 'approval-1', work_id: 'work-1', step_id: null, required_authority: 'external',
        requested_action: 'Send the report', status: 'pending', created_at: 'now',
      }],
    }))
    renderDetail()
    expect(await screen.findByText('Waiting for authority approval')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve authority' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Confirm payload' })).not.toBeInTheDocument()
  })

  it.each([
    ['paused', 'Work is paused', 'Paused; nothing else will start.', 'Resume'],
    ['pausing', 'Pausing at a safe point', 'Stopping at the next safe point.', undefined],
  ])('renders truthful %s state', async (phase, title, execution, control) => {
    mockedApi.mockResolvedValue(workDetail({ phase, actions: control ? ['run'] : ['pause'] }))
    renderDetail()
    expect(await screen.findByText(title)).toBeInTheDocument()
    expect(screen.getByText(execution)).toBeInTheDocument()
    if (control) expect(screen.getByRole('button', { name: control })).toBeEnabled()
  })

  it('renders failed state and failed execution without inventing an outcome', async () => {
    mockedApi.mockResolvedValue(workDetail({
      status: 'failed', phase: 'terminal',
      executions: [{ id: 'execution-1', step_id: 'step-1', capability: 'communication.email.send', capability_version: '1', provider: 'smtp', attempt: 1, status: 'failed', error: 'Provider rejected request', receipt: {}, started_at: 'now', ended_at: 'now', input_artifact_ids: [], output_artifact_ids: [] }],
    }))
    renderDetail()
    expect(await screen.findByText('Work failed')).toBeInTheDocument()
    expect(screen.getByText(/Provider rejected request/)).toBeInTheDocument()
    expect(screen.getByText(/I couldn't finish this/)).toBeInTheDocument()
  })

  it('renders completed artifacts as deliverables and keeps empty evidence honest', async () => {
    mockedApi.mockResolvedValue(workDetail({
      status: 'completed', phase: 'terminal',
      artifacts: [{ id: 'artifact-1', step_id: 'step-1', kind: 'report', sha256: 'hash', metadata: {}, created_at: 'now', payload: { title: 'Report' } }],
    }))
    renderDetail()
    expect(await screen.findByText('Work completed')).toBeInTheDocument()
    expect(screen.getByText('report')).toBeInTheDocument()
    expect(screen.getAllByText('No evidence claims yet.').length).toBeGreaterThan(0)
  })

  it('renders only lifecycle controls advertised by backend actions', async () => {
    mockedApi.mockResolvedValue(workDetail({ phase: 'paused', actions: ['run', 'archive'] }))
    renderDetail()
    expect(await screen.findByRole('button', { name: 'Resume' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Archive' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
  })

  it('renders activity as a chronological operator log and retains technical inspection', async () => {
    mockedApi.mockResolvedValue(workDetail({
      phase: 'waiting',
      events: [{ id: 1, name: 'work.paused', step_id: null, execution_id: null, payload: {}, created_at: '2026-01-01T12:00:00Z' }],
    }))
    renderDetail()
    expect(await screen.findByText('Paused at a safe point')).toBeInTheDocument()
    expect(screen.getByText('2026-01-01T12:00:00Z')).toBeInTheDocument()
    expect(screen.getByText('Inspect technical details')).toBeInTheDocument()
  })

  it('keeps one connection during SSE updates and reconnects from the event cursor', async () => {
    mockedApi.mockResolvedValue(workDetail({ phase: 'waiting' }))
    renderDetail()
    await screen.findByText('No runtime activity yet.')
    expect(FakeEventSource.instances).toHaveLength(1)
    act(() => FakeEventSource.instances[0]?.dispatchEvent(new MessageEvent('work.completed', { lastEventId: '12' })))
    await waitFor(() => expect(mockedApi.mock.calls.length).toBeGreaterThan(1))
    expect(FakeEventSource.instances).toHaveLength(1)
    vi.useFakeTimers()
    act(() => { FakeEventSource.instances[0]?.onerror?.(new Event('error')); vi.advanceTimersByTime(1000) })
    expect(FakeEventSource.instances[1]?.url).toBe('/api/work/work-1/events/stream?after=12')
    vi.useRealTimers()
  })
})
