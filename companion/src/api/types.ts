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

export type WorkStep = {
  id: string
  ordinal: number
  description: string
  capability: string | null
  capability_version: string | null
  status: string
  dependencies: string[]
  input_artifact_ids: string[]
}

export type WorkArtifact = {
  id: string
  step_id: string | null
  kind: string
  sha256: string
  metadata: Record<string, unknown>
  created_at: string
  payload: unknown
}

export type WorkClaim = {
  id: string
  step_id: string | null
  kind: string
  subject: string
  value: unknown
  evidence_artifact_ids: string[]
  confidence: number | null
  created_at: string
}

export type WorkExecution = {
  id: string
  step_id: string
  capability: string
  capability_version: string
  provider: string | null
  attempt: number
  status: string
  error: string | null
  receipt: Record<string, unknown>
  started_at: string
  ended_at: string | null
  input_artifact_ids: string[]
  output_artifact_ids: string[]
}

export type WorkEvent = {
  id: number
  name: string
  step_id: string | null
  execution_id: string | null
  payload: Record<string, unknown>
  created_at: string
}

export type WorkCriterion = {
  id: string
  ordinal: number
  text: string
  status: string
  evidence_artifact_ids: string[]
  note: string | null
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
  steps: WorkStep[]
  pending_approvals: PendingApproval[]
  pending_confirmations: PendingConfirmation[]
  artifacts: WorkArtifact[]
  claims: WorkClaim[]
  executions: WorkExecution[]
  events: WorkEvent[]
  criteria: WorkCriterion[]
  actions: string[]
}

export type BriefSource = {
  conversation_id: string
  until_turn_id: string | null
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
  source?: BriefSource
}

export type UnsupportedBrief = {
  status: 'unsupported'
  objective: string
  reason: string
  closest_capability: string | null
  source?: BriefSource
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
