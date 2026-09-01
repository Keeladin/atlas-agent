export type Decision = 'NO' | 'YES' | 'CONFIRM'

export type ActionOccurrence = {
  occurrence_id: string
  capability_id: string
  operation: string
  scope: string
  payload_sha256: string
  policy_decision: Decision
  policy_revision: number
  status: 'blocked' | 'pending_confirmation' | 'executing' | 'succeeded' | 'failed' | 'uncertain' | 'expired' | 'cancelled'
  work_id?: string | null
  step_id?: string | null
  summary?: string | null
  result?: unknown
  receipt?: Record<string, unknown>
  error_code?: string | null
  error?: string | null
  created_at: string
  confirmed_at?: string | null
  executed_at?: string | null
  completed_at?: string | null
}

export type Conversation = { conversation_id: string; title: string; created_at: string; updated_at: string }
export type ChatTurn = { turn_id: string; conversation_id: string; role: string; content: string; metadata: Record<string, unknown>; created_at?: string }
export type WorkStep = { step_id: string; ordinal: number; description: string; capability_id: string; input: Record<string, unknown>; status: string; occurrence_id?: string | null; output?: unknown; error?: string | null }
export type WorkItem = { work_id: string; display_ref?: string | null; artifact_class?: string | null; workflow_class?: string | null; objective: string; status: string; owner_principal_id: string; created_at: string; updated_at: string; metadata: Record<string, unknown>; steps?: WorkStep[] }
export type Cadence = { cadence_id: string; name: string; objective: string; schedule: Record<string, unknown>; steps: Array<Record<string, unknown>>; enabled: boolean; next_run_at?: string | null; last_run_at?: string | null; last_work_id?: string | null }
export type PolicyRule = { event_id: string; sequence: number; principal_id: string; scope: string; operation: string; decision: Decision; reason?: string | null; created_at: string }
export type Provider = { key: string; kind: string; model: string; base_url?: string | null; enabled: boolean; local: boolean; priority: number; credential_configured: boolean; metadata: Record<string, unknown>; updated_at: string }
export type WebProvider = { key: string; kind: 'jina' | 'brave' | 'tavily' | 'serper'; enabled: boolean; priority: number; credential_configured: boolean; metadata: Record<string, unknown>; updated_at: string }
export type MCPServer = { server_id: string; display_name: string; kind: 'mcp' | 'n8n'; transport: 'streamable-http' | 'stdio'; url?: string | null; command?: string | null; args: readonly string[]; cwd?: string | null; enabled: boolean; credential_configured: boolean; timeout_sec: number; read_timeout_sec: number; last_error?: string | null; last_discovered_at?: string | null; discovered_tool_count: number }
export type SourceRoot = { root_id: string; provider_namespace: string; host_path: string; display_name: string; quarantine_relative_path?: string | null; enabled: boolean; updated_at: string }
export type JsonSchema = {
  type?: string | string[]
  title?: string
  description?: string
  format?: string
  enum?: unknown[]
  default?: unknown
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  oneOf?: JsonSchema[]
  anyOf?: JsonSchema[]
  additionalProperties?: boolean | JsonSchema
  [key: string]: unknown
}
export type Capability = { id: string; description: string; operation: string; effect_class: string; input_schema: JsonSchema; source: string; tags: string[]; available: boolean; availability_reason: string; policy_decision: Decision; policy_revision: number; scope_hint?: string | null; metadata: Record<string, unknown> }

export type MemoryItem = { item_id: string; principal_id: string; title: string; content: string; grounding_excerpt?: string | null; source_ref?: string | null; metadata: Record<string, unknown>; state: 'active' | 'superseded' | 'retracted'; supersedes?: string | null; created_at: string; updated_at: string; retracted_at?: string | null; score?: number }
