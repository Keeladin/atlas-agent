export type WorkListItem = {
  work_id: string
  objective: string
  status: string
  authority_scope: string
  created_at: string
  updated_at: string
  archived?: boolean
  paused?: boolean
}

export type PendingApproval = {
  id: string
  work_id: string
  step_id: string | null
  required_authority: string
  requested_action: string
  status: string
  created_at: string
}

export type PendingConfirmation = {
  id: string
  work_id: string
  step_id: string
  capability_id: string
  payload_sha256: string
  summary: string
  payload: Record<string, unknown>
  status: string
  created_at: string
}

export type WorkDetail = {
  work_id: string
  objective: string
  status: string
  authority_scope: string
  created_at: string
  updated_at: string
  phase: string
  blocking: {
    kind: string
    message: string
    confirmation_ids?: string[]
    approval_ids?: string[]
    execution_ids?: string[]
  } | null
  contract: Record<string, unknown>
  capabilities: Array<Record<string, unknown>>
  steps: Array<{
    id: string
    ordinal: number
    description: string
    capability: string | null
    capability_version: string | null
    status: string
    dependencies: string[]
    input_artifact_ids: string[]
  }>
  pending_approvals: PendingApproval[]
  pending_confirmations: PendingConfirmation[]
  artifacts: Array<Record<string, unknown>>
  claims: Array<Record<string, unknown>>
  executions: Array<Record<string, unknown>>
  events: Array<Record<string, unknown>>
  criteria: Array<Record<string, unknown>>
  actions: string[]
}

export type TaskBrief = {
  status?: 'brief'
  objective: string
  capabilities: string[]
  required_authority: string
  expected_effect: string
  constraints: string[]
  deliverable_kind?: string | null
  notes?: string | null
}

export type UnsupportedBrief = {
  status: 'unsupported'
  objective: string
  reason: string
  closest_capability: string | null
}

export type UnavailableAcceptance = {
  status: 'unavailable'
  objective: string
  reason: string
  capabilities: string[]
  unarmed: string[]
  mismatches: string[]
}

export type BriefResult = TaskBrief | UnsupportedBrief

export function isUnsupportedBrief(
  value: BriefResult | TaskBrief,
): value is UnsupportedBrief {
  return (
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    value.status === 'unsupported'
  )
}

export function isUnavailableAcceptance(
  value: unknown,
): value is UnavailableAcceptance {
  return (
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    (value as { status?: unknown }).status === 'unavailable'
  )
}

export type Conversation = {
  id: string
  title: string
  turn_count: number
  pinned?: boolean
  archived?: boolean
  archived_at?: string | null
  turns?: Array<{
    id: string
    role: string
    content: string
    created_at: string
  }>
}
