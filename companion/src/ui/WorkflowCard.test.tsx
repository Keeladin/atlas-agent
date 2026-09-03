import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActionOccurrence } from '../api/types'
import { WorkflowCard } from './WorkflowCard'
import { workflowVariant } from './workflowPresentation'

function occurrence(capability_id: string, result: unknown): ActionOccurrence {
  return {
    occurrence_id: 'occ_1', capability_id, operation: 'x', scope: 'atlas/cadence', payload_sha256: 'h',
    policy_decision: 'YES', policy_revision: 1, status: 'succeeded', result, created_at: '2026-09-03T06:00:00+00:00',
  }
}

const CADENCE_RESULT = {
  cadence_id: 'cadence_1', name: 'Morning brief', objective: 'brief', enabled: true,
  schedule: { kind: 'daily', hour: 8, minute: 0 }, kind: 'work_template',
  steps: [{ capability_id: 'knowledge.search', input: { query: 'brief' } }],
  next_run_at: '2026-09-04T06:00:00+00:00',
}

function renderCard(action: ActionOccurrence) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><WorkflowCard action={action} /></MemoryRouter></QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('WorkflowCard', () => {
  it('maps only workflow capabilities to a variant', () => {
    expect(workflowVariant('cadence.create')).toBe('cadence-created')
    expect(workflowVariant('cadence.update')).toBe('cadence-updated')
    expect(workflowVariant('cadence.run_now')).toBe('cadence-run')
    expect(workflowVariant('work.create')).toBe('work-created')
    expect(workflowVariant('work.get')).toBeNull()
    expect(workflowVariant('knowledge.search')).toBeNull()
  })

  it('renders a cadence variant with schedule, steps, and duty controls', () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ capabilities: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    renderCard(occurrence('cadence.create', CADENCE_RESULT))
    expect(screen.getByText('Standing duty created')).toBeInTheDocument()
    expect(screen.getByText('Morning brief')).toBeInTheDocument()
    expect(screen.getByText(/Daily at 08:00/)).toBeInTheDocument()
    expect(screen.getByText('knowledge.search')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run now' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Disable' })).toBeInTheDocument()
  })

  it('does not offer cadence controls on a work variant', () => {
    renderCard(occurrence('work.create', { work_id: 'work_1', objective: 'Do the thing', steps: [{ capability_id: 'web.search', input: {} }] }))
    expect(screen.getByText('Work created')).toBeInTheDocument()
    expect(screen.getByText('Do the thing')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run now' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Disable' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open full Work' })).toBeInTheDocument()
  })

  it('links a manual run to the Work it created without offering schedule controls', () => {
    renderCard(occurrence('cadence.run_now', { cadence_id: 'cadence_1', kind: 'work_template', work_id: 'work_9', trigger: 'manual' }))
    expect(screen.getByText('Ran now, schedule unchanged')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the Work this run created' }).getAttribute('href')).toContain('work_9')
    expect(screen.queryByRole('button', { name: 'Disable' })).not.toBeInTheDocument()
  })

  it('saves one step input through cadence.update', async () => {
    const calls: Array<{ url: string; body: unknown }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      const body = url.includes('/api/capabilities/cadence.update/invoke')
        ? { action: { occurrence_id: 'occ_2', capability_id: 'cadence.update', status: 'succeeded', result: CADENCE_RESULT } }
        : { capabilities: [{ id: 'knowledge.search', description: 'Search', operation: 'search', effect_class: 'none', input_schema: { type: 'object', properties: { query: { type: 'string' } } }, source: 'knowledge', tags: [], available: true, availability_reason: 'available', policy_decision: 'YES', policy_revision: 1, metadata: {} }] }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    renderCard(occurrence('cadence.create', CADENCE_RESULT))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit input' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save step input' }))
    await waitFor(() => expect(calls.some(call => call.url.includes('/api/capabilities/cadence.update/invoke'))).toBe(true))
    const update = calls.find(call => call.url.includes('cadence.update'))?.body as { input: { cadence_id: string; steps: unknown[] } }
    expect(update.input.cadence_id).toBe('cadence_1')
    expect(update.input.steps).toHaveLength(1)
  })
})
