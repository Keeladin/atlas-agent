import type { WorkDetail } from '../api/types'

export function workDetail(overrides: Partial<WorkDetail> = {}): WorkDetail {
  return {
    work_id: 'work-1',
    objective: 'Send the report',
    status: 'active',
    authority_scope: 'external',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    phase: 'running',
    blocking: null,
    contract: {},
    capabilities: [],
    steps: [],
    pending_approvals: [],
    pending_confirmations: [],
    artifacts: [],
    claims: [],
    executions: [],
    events: [],
    criteria: [],
    actions: [],
    ...overrides,
  }
}
