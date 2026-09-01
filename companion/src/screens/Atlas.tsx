import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionOccurrence, Capability, Decision, MCPServer, PolicyRule, Provider, SourceRoot, WebProvider } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { FactList, InspectorPanel, InspectorSection, OperationalRibbon, OperationalRow, StatusLamp } from '../ui/OperationsPrimitives'
import type { LampTone } from '../ui/operationState'
import { Panel } from '../ui/Panel'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Sheet } from '../ui/Sheet'
import { Workspace } from '../ui/Workspace'
import { CapabilityBrowser } from './CapabilityBrowser'
import { capabilityLensFor, policyLensFor, type PolicyLens } from './policyLens'

type SystemState = {
  version: string
  policy_revision: number
  providers: Provider[]
  web_providers: WebProvider[]
  mcp_servers: MCPServer[]
  source_roots: SourceRoot[]
  capabilities: Capability[]
  connections: Array<Record<string, unknown>>
  service_bindings: Array<Record<string, unknown>>
  pending_confirmations: ActionOccurrence[]
  host: HostState
}

type HostStatus = {
  hostname?: string
  kernel?: string
  pid?: number
  uid?: number
  invocation_id?: string | null
  timestamp?: string
}

type HostResources = {
  load_1?: number
  load_5?: number
  load_15?: number
  cpu_count?: number | null
  memory?: Record<string, string | null | undefined>
  timestamp?: string
}

type HostFilesystem = { path?: string; total?: number; used?: number; free?: number }
type HostStorage = { filesystems?: HostFilesystem[]; timestamp?: string }
type HostState = { status?: HostStatus; resources?: HostResources; storage?: HostStorage }
type AtlasBack = { path: string; parent?: AtlasBack }

const ATLAS_TABS = [
  { to: '/atlas', label: 'Overview', end: true },
  { to: '/atlas/runtime', label: 'Runtime' },
  { to: '/atlas/policies', label: 'Policies' },
  { to: '/atlas/models', label: 'Providers' },
  { to: '/atlas/connections', label: 'Connections' },
  { to: '/atlas/filesystem', label: 'Filesystem' },
  { to: '/atlas/capabilities', label: 'Capabilities' },
]

function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function VerificationResult({ result }: { result: unknown }) {
  const data = result && typeof result === 'object' ? result as Record<string, unknown> : {}
  const successful = data.ok !== false
  const facts = [
    { label: 'Provider', value: data.provider },
    { label: 'Adapter', value: data.kind },
    { label: 'Model', value: data.model },
    { label: 'Results returned', value: data.result_count },
  ].filter(item => item.value !== undefined && item.value !== null)
    .map(item => ({ label: item.label, value: String(item.value), mono: item.label !== 'Results returned' }))
  return <div className={`provider-verification-result ${successful ? 'success' : 'failed'}`} role="status">
    <StatusLamp tone={successful ? 'green' : 'red'} />
    <div><strong>Verification {successful ? 'successful' : 'failed'}</strong><span>{successful ? 'The provider responded successfully.' : 'The provider did not pass verification.'}</span>{facts.length ? <FactList items={facts} /> : null}</div>
  </div>
}

function useAtlasControl() {
  const qc = useQueryClient()
  const system = useQuery({ queryKey: ['system'], queryFn: () => api<SystemState>('/api/system'), refetchInterval: 10000 })
  const policy = useQuery({ queryKey: ['policy'], queryFn: () => api<{ revision: number; rules: PolicyRule[] }>('/api/policy') })
  const refresh = async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['system'] }), qc.invalidateQueries({ queryKey: ['policy'] }), qc.invalidateQueries({ queryKey: ['pending-actions'] }), qc.invalidateQueries({ queryKey: ['knowledge-generations'] })]) }
  return { system, policy, refresh }
}

export function AtlasPage({ title, subtitle, children, banner, headerActions }: { title: string; subtitle: string; children: ReactNode; banner?: ReactNode; headerActions?: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const back = (location.state as { atlasBack?: AtlasBack } | null)?.atlasBack
  const goBack = () => back ? navigate(back.path, { state: back.parent ? { atlasBack: back.parent } : null }) : navigate('/atlas')
  const crumb = location.pathname === '/atlas' ? undefined : <button type="button" className="atlas-back" onClick={goBack}><span aria-hidden>←</span> Back</button>
  return <Workspace className="operations-workspace atlas-control-workspace atlas-technical-workspace" title={title} subtitle={subtitle} crumb={crumb} headerActions={headerActions} tabs={<SegmentedNav items={ATLAS_TABS} />} banner={banner}><div className="atlas-page-body">{children}</div></Workspace>
}

export function Atlas() {
  const { system } = useAtlasControl()
  const state = system.data
  const enabledProviders = state?.providers.filter(provider => provider.enabled).length ?? 0
  const availableCapabilities = state?.capabilities.filter(capability => capability.available).length ?? 0
  const pending = state?.pending_confirmations.length ?? 0
  const runtimeTone: LampTone = system.isError ? 'red' : state ? 'green' : 'dim'
  const runtimeLabel = system.isError ? 'Unavailable' : state ? 'Available' : 'Checking'
  const unavailableCapabilities = state?.capabilities.filter(capability => !capability.available) ?? []
  const missingCredentials = state?.providers.filter(provider => provider.enabled && !provider.local && !provider.credential_configured) ?? []
  const discoveryErrors = state?.mcp_servers.filter(server => Boolean(server.last_error)) ?? []
  const attention = [
    ...state?.pending_confirmations.map(item => ({ key: item.occurrence_id, tone: 'red' as const, title: item.summary || item.capability_id, detail: `${item.operation} · ${item.scope}`, status: 'confirmation pending', to: '/atlas/runtime' })) ?? [],
    ...discoveryErrors.map(server => ({ key: server.server_id, tone: 'red' as const, title: `${server.display_name} discovery error`, detail: server.last_error ?? server.server_id, status: 'discovery error', to: '/atlas/connections' })),
    ...missingCredentials.map(provider => ({ key: provider.key, tone: 'amber' as const, title: `${provider.key} has no configured credential`, detail: `${provider.kind} · ${provider.model}`, status: 'not configured', to: '/atlas/models' })),
    ...(unavailableCapabilities.length ? [{ key: 'unavailable-capabilities', tone: 'amber' as const, title: `${unavailableCapabilities.length} unavailable capabilities`, detail: 'Inspect exact availability reasons in the capability browser.', status: 'unavailable', to: '/atlas/capabilities' }] : []),
  ]
  const topology: Array<{ to: string; label: string; detail: string; tone: LampTone }> = [
    { to: '/atlas/runtime', label: 'Runtime', detail: state ? `v${state.version}` : runtimeLabel, tone: runtimeTone },
    { to: '/atlas/policies', label: 'Policy', detail: state ? `revision ${state.policy_revision}` : '—', tone: state ? 'green' as const : 'dim' as const },
    { to: '/atlas/models', label: 'Providers', detail: state ? `${enabledProviders} / ${state.providers.length} enabled` : '—', tone: state && enabledProviders ? 'green' as const : 'dim' as const },
    { to: '/atlas/connections', label: 'Connections', detail: state ? `${state.mcp_servers.length} configured` : '—', tone: discoveryErrors.length ? 'red' as const : state?.mcp_servers.length ? 'green' as const : 'dim' as const },
    { to: '/atlas/filesystem', label: 'Filesystem', detail: state ? `${state.source_roots.length} roots` : '—', tone: state?.source_roots.some(root => root.enabled) ? 'green' as const : 'dim' as const },
    { to: '/atlas/capabilities', label: 'Capabilities', detail: state ? `${availableCapabilities} / ${state.capabilities.length} available` : '—', tone: unavailableCapabilities.length ? 'amber' as const : state?.capabilities.length ? 'green' as const : 'dim' as const },
  ]
  const snapshot = <InspectorPanel title="Runtime snapshot" eyebrow="Live control plane" status={<StatusLamp tone={runtimeTone} label={runtimeLabel} />}>
    <InspectorSection title="Process"><FactList items={[
      { label: 'Version', value: state?.version ?? '—', mono: true },
      { label: 'Host', value: state?.host.status?.hostname ?? '—', mono: true },
      { label: 'PID', value: state?.host.status?.pid ?? '—', mono: true },
      { label: 'Observed', value: when(state?.host.status?.timestamp), mono: true },
    ]} /></InspectorSection>
    <InspectorSection title="Control"><FactList items={[
      { label: 'Policy revision', value: state?.policy_revision ?? '—', mono: true },
      { label: 'Pending', value: pending },
      { label: 'Providers', value: state ? `${enabledProviders} / ${state.providers.length} enabled` : '—' },
      { label: 'Capabilities', value: state ? `${availableCapabilities} / ${state.capabilities.length} available` : '—' },
    ]} /></InspectorSection>
    <InspectorSection title="Navigation"><Link className="button-link" to="/atlas/runtime">Open runtime</Link></InspectorSection>
  </InspectorPanel>
  return <AtlasPage title="Atlas" subtitle="Runtime, authority, and technical capability." banner={<OperationalRibbon items={[
    { label: 'Runtime', value: runtimeLabel, tone: runtimeTone },
    { label: 'Policy revision', value: state?.policy_revision ?? '—', tone: state ? 'green' : 'dim' },
    { label: 'Providers enabled', value: state ? `${enabledProviders} / ${state.providers.length}` : '—', tone: state && enabledProviders ? 'green' : 'dim' },
    { label: 'Services configured', value: state?.mcp_servers.length ?? '—', tone: state?.mcp_servers.length ? 'green' : 'dim' },
    { label: 'Capabilities available', value: state ? `${availableCapabilities} / ${state.capabilities.length}` : '—', tone: unavailableCapabilities.length ? 'amber' : state?.capabilities.length ? 'green' : 'dim' },
    { label: 'Confirmations pending', value: pending, tone: pending ? 'red' : 'dim' },
  ]} />}>
    <div className="atlas-overview-grid">
      <div className="atlas-overview-main">
        <section className="ops-surface atlas-attention-surface"><header className="ops-surface-head"><div><span className="eyebrow">Configuration attention</span><strong>{attention.length ? `${attention.length} exact runtime facts to inspect` : 'No configuration exceptions reported'}</strong></div></header><div className="atlas-attention-list">{attention.map(item => <Link to={item.to} key={`${item.key}:${item.status}`}><OperationalRow lamp={item.tone} label={item.title} secondary={item.detail} status={<span className={`chip ${item.tone === 'red' ? 'failed' : 'running'}`}>{item.status}</span>} /></Link>)}{!attention.length ? <div className="empty-state compact"><strong>No pending confirmations, discovery errors, missing provider credentials, or unavailable capabilities.</strong></div> : null}</div></section>
        <section className="ops-surface atlas-topology-surface"><header className="ops-surface-head"><div><span className="eyebrow">System topology</span><strong>One runtime, six technical control surfaces</strong></div></header><div className="atlas-topology-grid">{topology.map(item => <Link to={item.to} key={item.to}><StatusLamp tone={item.tone} /><span><strong>{item.label}</strong><small className="mono">{item.detail}</small></span><span aria-hidden>→</span></Link>)}</div></section>
      </div>
      {snapshot}
    </div>
  </AtlasPage>
}

type Generation = {
  generation_id: string
  extractor_config_id: string
  segmenter_config_id: string
  mechanisms: string[]
  state: 'building' | 'verifying' | 'candidate' | 'active' | 'retired' | 'failed'
  verification: { passages?: number; sources?: number; ok?: boolean } | null
  created_at: string
  activated_at: string | null
}

function KnowledgeGenerations({ onDone }: { onDone: () => Promise<unknown> }) {
  const generations = useQuery({ queryKey: ['knowledge-generations'], queryFn: () => api<{ generations: Generation[] }>('/api/knowledge/generations') })
  const [message, setMessage] = useState<string | null>(null)
  const [pending, setPending] = useState<ActionOccurrence | null>(null)
  const activate = useMutation({
    mutationFn: (generationId: string) => api<{ action: ActionOccurrence }>('/api/capabilities/knowledge.activate_generation/invoke', { method: 'POST', body: JSON.stringify({ input: { generation_id: generationId } }) }),
    onSuccess: async ({ action }) => {
      setPending(action.status === 'pending_confirmation' ? action : null)
      setMessage(action.status === 'pending_confirmation' ? 'Confirm to make this the default retrieval corpus.' : `Activation ${action.status}.`)
      await onDone()
    },
    onError: error => setMessage(error instanceof Error ? error.message : String(error)),
  })
  const rows = generations.data?.generations ?? []
  return <Panel title="Knowledge generations">
    <p className="meta">Indexed knowledge is served from one active generation. Passages and provenance are durable; the physical index is rebuildable.</p>
    {!rows.length ? <div className="empty-state compact"><strong>No generations yet</strong><span>Indexing an extracted artifact creates the first generation.</span></div> : null}
    <div className="stack">{rows.map(row => <article className="knowledge-item" key={row.generation_id}>
      <div className="row-head"><strong className="mono">{row.generation_id.slice(0, 22)}…</strong><span className={`chip ${row.state === 'active' ? 'done' : ''}`}>{row.state}</span></div>
      <div className="meta mono">{row.extractor_config_id} · {row.segmenter_config_id} · {row.mechanisms.join(', ')}</div>
      {row.verification ? <div className="meta">{row.verification.passages ?? 0} passages across {row.verification.sources ?? 0} sources · verification {row.verification.ok ? 'passed' : 'failed'}</div> : <div className="meta">Not yet verified.</div>}
      {row.state === 'candidate' ? <div className="actions"><button type="button" disabled={activate.isPending} onClick={() => activate.mutate(row.generation_id)}>Activate</button></div> : null}
    </article>)}</div>
    {message ? <p className="meta">{message}</p> : null}
    {pending ? <ConfirmationCard item={pending} onDone={async () => { setPending(null); setMessage(null); await onDone() }} /> : null}
  </Panel>
}

export function AtlasRuntime() {
  const { system, refresh } = useAtlasControl()
  const state = system.data
  const filesystems = state?.host.storage?.filesystems ?? []
  return <AtlasPage title="Runtime" subtitle="Live process and host truth. Observation does not grant authority." banner={<OperationalRibbon items={[
    { label: 'API state', value: system.isError ? 'Unavailable' : state ? 'Available' : 'Checking', tone: system.isError ? 'red' : state ? 'green' : 'dim' },
    { label: 'Version', value: state?.version ?? '—', tone: state ? 'green' : 'dim' },
    { label: 'Policy revision', value: state?.policy_revision ?? '—', tone: state ? 'green' : 'dim' },
    { label: 'Capabilities', value: state?.capabilities.length ?? '—', tone: state?.capabilities.length ? 'green' : 'dim' },
    { label: 'Storage observed', value: filesystems.length, tone: filesystems.length ? 'green' : 'dim' },
    { label: 'Confirmations', value: state?.pending_confirmations.length ?? 0, tone: state?.pending_confirmations.length ? 'red' : 'dim' },
  ]} />}><div className="atlas-runtime-page"><RuntimeOverview state={state} /><div className="atlas-runtime-secondary"><Pending items={state?.pending_confirmations ?? []} onDone={refresh} /><KnowledgeGenerations onDone={refresh} /></div></div></AtlasPage>
}

export function AtlasPolicies() {
  const { system, policy, refresh } = useAtlasControl()
  const rules = policy.data?.rules ?? []
  return <AtlasPage title="Policies" subtitle="Owner authority for consequential actions." banner={<OperationalRibbon items={[
    { label: 'Revision', value: policy.data?.revision ?? '—', tone: policy.data ? 'green' : 'dim' },
    { label: 'Rules', value: rules.length, tone: rules.length ? 'green' : 'dim' },
    { label: 'YES', value: rules.filter(rule => rule.decision === 'YES').length },
    { label: 'CONFIRM', value: rules.filter(rule => rule.decision === 'CONFIRM').length, tone: rules.some(rule => rule.decision === 'CONFIRM') ? 'amber' : 'dim' },
    { label: 'NO', value: rules.filter(rule => rule.decision === 'NO').length },
  ]} />}><PolicyPanel rules={rules} capabilities={system.data?.capabilities ?? []} servers={system.data?.mcp_servers ?? []} revision={policy.data?.revision ?? 0} onDone={refresh} /></AtlasPage>
}

export function AtlasModels() {
  const { system, refresh } = useAtlasControl()
  const providers = system.data?.providers ?? []
  const webProviders = system.data?.web_providers ?? []
  return <AtlasPage title="Providers" subtitle="Replaceable model and web access beneath stable Atlas capabilities." banner={<OperationalRibbon items={[
    { label: 'Model providers', value: providers.length, tone: providers.length ? 'green' : 'dim' },
    { label: 'Models enabled', value: providers.filter(provider => provider.enabled).length, tone: providers.some(provider => provider.enabled) ? 'green' : 'dim' },
    { label: 'Web search providers', value: webProviders.length, tone: webProviders.length ? 'green' : 'dim' },
    { label: 'Web search ready', value: webProviders.some(provider => provider.enabled && provider.credential_configured) ? 'Yes' : 'No', tone: webProviders.some(provider => provider.enabled && provider.credential_configured) ? 'green' : 'amber' },
  ]} />}><div className="atlas-provider-stack"><Providers providers={providers} onDone={refresh} /><WebProviders providers={webProviders} onDone={refresh} /></div></AtlasPage>
}

export function AtlasIntegrations() {
  const { system, refresh } = useAtlasControl()
  const state = system.data
  const servers = state?.mcp_servers ?? []
  return <AtlasPage title="Connections" subtitle="External services, accounts, and discovered capability." banner={<OperationalRibbon items={[
    { label: 'Services configured', value: servers.length, tone: servers.length ? 'green' : 'dim' },
    { label: 'Enabled', value: servers.filter(server => server.enabled).length, tone: servers.some(server => server.enabled) ? 'green' : 'dim' },
    { label: 'Tools discovered', value: servers.reduce((total, server) => total + server.discovered_tool_count, 0), tone: servers.some(server => server.discovered_tool_count) ? 'green' : 'dim' },
    { label: 'Discovery errors', value: servers.filter(server => server.last_error).length, tone: servers.some(server => server.last_error) ? 'red' : 'dim' },
    { label: 'External accounts', value: state?.connections.length ?? 0, tone: state?.connections.length ? 'green' : 'dim' },
  ]} />}><Mcp servers={servers} capabilities={state?.capabilities ?? []} connections={state?.connections ?? []} bindings={state?.service_bindings ?? []} onDone={refresh} /></AtlasPage>
}

export function AtlasFilesystem() {
  const { system, refresh } = useAtlasControl()
  const roots = system.data?.source_roots ?? []
  return <AtlasPage title="Filesystem" subtitle="Enrolled roots and managed host paths." banner={<OperationalRibbon items={[
    { label: 'Roots enrolled', value: roots.length, tone: roots.length ? 'green' : 'dim' },
    { label: 'Enabled', value: roots.filter(root => root.enabled).length, tone: roots.some(root => root.enabled) ? 'green' : 'dim' },
    { label: 'Disabled', value: roots.filter(root => !root.enabled).length, tone: roots.some(root => !root.enabled) ? 'dim' : undefined },
    { label: 'Quarantine configured', value: roots.filter(root => root.quarantine_relative_path).length, tone: roots.some(root => root.quarantine_relative_path) ? 'green' : 'dim' },
  ]} />}><Roots roots={roots} capabilities={system.data?.capabilities ?? []} onDone={refresh} /></AtlasPage>
}

export function AtlasCapabilities() {
  const { system, refresh } = useAtlasControl()
  const capabilities = system.data?.capabilities ?? []
  return <AtlasPage title="Capabilities" subtitle="Live capability inventory, exact authority, and execution." banner={<OperationalRibbon items={[
    { label: 'Discovered', value: capabilities.length, tone: capabilities.length ? 'green' : 'dim' },
    { label: 'Available', value: capabilities.filter(item => item.available).length, tone: capabilities.some(item => item.available) ? 'green' : 'dim' },
    { label: 'Unavailable', value: capabilities.filter(item => !item.available).length, tone: capabilities.some(item => !item.available) ? 'amber' : 'dim' },
    { label: 'YES', value: capabilities.filter(item => item.policy_decision === 'YES').length },
    { label: 'CONFIRM', value: capabilities.filter(item => item.policy_decision === 'CONFIRM').length, tone: capabilities.some(item => item.policy_decision === 'CONFIRM') ? 'amber' : 'dim' },
    { label: 'NO', value: capabilities.filter(item => item.policy_decision === 'NO').length },
  ]} />}><CapabilityBrowser items={capabilities} servers={system.data?.mcp_servers ?? []} onDone={refresh} /></AtlasPage>
}

function formatBytes(value?: number) {
  if (value == null || !Number.isFinite(value)) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let amount = value
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1 }
  return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
}

function formatMem(value?: string | null) {
  if (!value) return '—'
  const match = value.match(/^([0-9.]+)\s*kB$/i)
  return match ? formatBytes(Number(match[1]) * 1024) : value
}

export function RuntimeOverview({ state }: { state?: SystemState }) {
  const status = state?.host?.status ?? {}
  const resources = state?.host?.resources ?? {}
  const storage = state?.host?.storage ?? {}
  const filesystems = storage.filesystems ?? []
  const invocation = status.invocation_id ? `${status.invocation_id.slice(0, 12)}…` : '—'

  const load = [resources.load_1, resources.load_5, resources.load_15].map(value => value == null ? '—' : value.toFixed(2)).join(' / ')
  return <div className="atlas-control-grid atlas-runtime-control">
    <section className="ops-rail-panel atlas-runtime-process"><div className="atlas-panel-heading"><span className="eyebrow">Process</span><StatusLamp tone={state ? 'green' : 'dim'} label={state ? 'Observed' : 'Unknown'} /></div><FactList items={[
      { label: 'Host', value: status.hostname ?? '—', mono: true },
      { label: 'Kernel', value: status.kernel ?? '—', mono: true },
      { label: 'PID / UID', value: `${status.pid ?? '—'} / ${status.uid ?? '—'}`, mono: true },
      { label: 'Invocation', value: invocation, mono: true },
      { label: 'Timestamp', value: when(status.timestamp), mono: true },
    ]} /></section>
    <section className="ops-surface atlas-runtime-telemetry"><header className="ops-surface-head"><div><span className="eyebrow">Telemetry</span><strong>Host resources and storage</strong></div><span className="meta mono">{resources.cpu_count ?? '—'} CPU</span></header><div className="atlas-telemetry-grid"><section><h3>Resources</h3><FactList items={[
      { label: 'Load 1 / 5 / 15', value: load, mono: true },
      { label: 'Memory available', value: formatMem(resources.memory?.MemAvailable), mono: true },
      { label: 'Memory total', value: formatMem(resources.memory?.MemTotal), mono: true },
      { label: 'Swap free', value: formatMem(resources.memory?.SwapFree), mono: true },
      { label: 'Observed', value: when(resources.timestamp), mono: true },
    ]} /></section><section><h3>Storage</h3><div className="runtime-storage">{filesystems.length ? filesystems.map((fs, index) => <div className="runtime-storage-row" key={`${fs.path ?? 'fs'}:${index}`}><strong className="mono">{fs.path ?? '—'}</strong><span>{formatBytes(fs.free)} free / {formatBytes(fs.total)}</span></div>) : <div className="empty-state compact"><strong>No storage telemetry</strong></div>}</div></section></div></section>
    <InspectorPanel title="Runtime identity" eyebrow="Control plane" status={<StatusLamp tone={state ? 'green' : 'dim'} label={state ? 'Available' : 'Unknown'} />}><InspectorSection title="Runtime"><FactList items={[
      { label: 'Version', value: state?.version ?? '—', mono: true },
      { label: 'Policy revision', value: state?.policy_revision ?? '—', mono: true },
      { label: 'Capabilities', value: state?.capabilities.length ?? '—' },
      { label: 'Storage samples', value: filesystems.length },
    ]} /></InspectorSection><InspectorSection title="Evidence"><details className="inspect runtime-evidence"><summary>Raw host evidence</summary><pre>{JSON.stringify(state?.host ?? {}, null, 2)}</pre></details></InspectorSection></InspectorPanel>
  </div>
}

function Pending({ items, onDone }: { items: ActionOccurrence[]; onDone: () => Promise<unknown> }) {
  return <Panel title="Pending confirmations" tone={items.length ? 'decision-confirm' : undefined} className="atlas-runtime-pending">{items.length ? <div className="stack">{items.map(item => <ConfirmationCard key={item.occurrence_id} item={item} onDone={onDone} />)}</div> : <div className="empty-state compact"><strong>No pending confirmations</strong><span>Exact-action confirmations will appear here.</span></div>}</Panel>
}

function policyDescription(rule: PolicyRule, capabilities: Capability[]) {
  const matching = capabilities.filter(capability => capability.operation === rule.operation && capability.scope_hint)
  const exact = matching.find(capability => capability.scope_hint === rule.scope)
  const related = matching.find(capability => rule.scope.startsWith(capability.scope_hint ?? '') || (capability.scope_hint ?? '').startsWith(rule.scope))
  if (exact?.description) return exact.description
  if (related?.description) return related.description
  const operation = rule.operation.replace(/[._-]+/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
  return `${operation} on this resource`
}

export function PolicyPanel({ rules, capabilities, servers, revision, onDone }: { rules: PolicyRule[]; capabilities: Capability[]; servers: MCPServer[]; revision: number; onDone: () => Promise<unknown> }) {
  const [lens, setLens] = useState<PolicyLens>('system')
  const [selectedKey, setSelectedKey] = useState('')
  const [scope, setScope] = useState('')
  const [operation, setOperation] = useState('')
  const [decision, setDecision] = useState<Decision>('CONFIRM')
  const [toolFilter, setToolFilter] = useState('')
  const save = useMutation({ mutationFn: (payload: { scope: string; operation: string; decision: Decision }) => api('/api/policy', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: onDone })
  function submit(event: FormEvent) { event.preventDefault(); save.mutate({ scope, operation, decision }) }

  const systemRules = rules.filter(rule => policyLensFor(rule, servers) === 'system')
  const toolCapabilities = capabilities.filter(capability => capabilityLensFor(capability, servers) === lens && capability.scope_hint?.startsWith('mcp/'))
  const filteredToolCapabilities = toolCapabilities.filter(capability => !toolFilter || `${capability.id} ${capability.description} ${String(capability.metadata?.tool_name ?? '')}`.toLowerCase().includes(toolFilter.toLowerCase()))
  const tabs: Array<{ id: PolicyLens; label: string; count: number }> = [
    { id: 'system', label: 'System', count: systemRules.length },
    { id: 'n8n', label: 'n8n Tools', count: capabilities.filter(capability => capabilityLensFor(capability, servers) === 'n8n' && capability.scope_hint?.startsWith('mcp/')).length },
    { id: 'mcp', label: 'MCP Tools', count: capabilities.filter(capability => capabilityLensFor(capability, servers) === 'mcp' && capability.scope_hint?.startsWith('mcp/')).length },
  ]
  const toolGroups = servers.filter(server => server.kind === lens).map(server => ({ server, tools: filteredToolCapabilities.filter(capability => capability.metadata?.server_id === server.server_id) })).filter(group => group.tools.length)
  const disconnectedTools = filteredToolCapabilities.filter(capability => !servers.some(server => server.server_id === capability.metadata?.server_id))
  const rows = lens === 'system'
    ? systemRules.map(rule => ({ key: `${rule.scope}:${rule.operation}`, description: policyDescription(rule, capabilities), id: capabilities.find(capability => capability.operation === rule.operation && capability.scope_hint === rule.scope)?.id ?? rule.event_id, scope: rule.scope, operation: rule.operation, decision: rule.decision, group: 'System' }))
    : filteredToolCapabilities.map(tool => ({ key: tool.id, description: tool.description, id: tool.id, scope: tool.scope_hint ?? '', operation: tool.operation, decision: tool.policy_decision, group: servers.find(server => server.server_id === tool.metadata?.server_id)?.display_name ?? 'Disconnected tools' }))
  const selected = rows.find(row => row.key === selectedKey) ?? rows[0] ?? null

  return <div className="atlas-control-grid atlas-policy-control">
    <section className="ops-rail-panel"><div className="atlas-panel-heading"><span className="eyebrow">Policy lenses</span><span className="mono meta">rev {revision}</span></div><div className="ops-filter-stack policy-tabs" role="tablist" aria-label="Policy lenses">{tabs.map(tab => <button key={tab.id} type="button" role="tab" aria-selected={lens === tab.id} className={lens === tab.id ? 'active' : ''} onClick={() => { setLens(tab.id); setSelectedKey('') }}><span>{tab.label}</span><strong>{tab.count}</strong></button>)}</div>{lens !== 'system' ? <div className="policy-provider-summary">{toolGroups.map(({ server, tools }) => <div key={server.server_id}><span><strong>{server.display_name}</strong><small>{server.kind}</small></span><span className="chip">{tools.length} tools</span></div>)}{disconnectedTools.length ? <div><span><strong>Disconnected tools</strong><small>Stored inventory</small></span><span className="chip">{disconnectedTools.length} tools</span></div> : null}</div> : <p className="ops-authority-note"><StatusLamp tone="amber" />Missing rules resolve to literal NO. Changes apply immediately to the running Atlas.</p>}</section>
    <section className="ops-surface atlas-policy-list"><header className="ops-surface-head"><div><span className="eyebrow">Authority rules</span><strong>{rows.length} controls in this lens</strong></div>{lens !== 'system' ? <input value={toolFilter} onChange={e => setToolFilter(e.target.value)} placeholder="Filter provider tools" aria-label="Filter provider tools" /> : null}</header><div className="atlas-table-head policy-table-columns"><span>Decision</span><span>Capability / tool</span><span>Operation</span><span>Scope</span></div><div className="atlas-table-body">{rows.map(row => <button type="button" className={`atlas-table-row policy-table-columns ${selected?.key === row.key ? 'active' : ''}`} key={row.key} onClick={() => setSelectedKey(row.key)}><span className={`authority-value decision-${row.decision.toLowerCase()}`}>{row.decision}</span><span className="atlas-table-copy"><strong>{row.description}</strong><small className="mono">{row.id}</small><small className="policy-syntax">{row.scope || '—'} · {row.operation}</small></span><span className="mono">{row.operation}</span><span className="mono">{row.scope || '—'}</span></button>)}{!rows.length ? <div className="empty-state"><strong>No {lens === 'system' ? 'system rules' : lens === 'n8n' ? 'n8n tools' : 'MCP tools'} in the current runtime snapshot.</strong></div> : null}</div></section>
    <InspectorPanel title={selected?.id ?? 'Select an authority rule'} eyebrow="Selected authority rule" status={selected ? <span className={`authority-value decision-${selected.decision.toLowerCase()}`}>{selected.decision}</span> : undefined}>
      {selected ? <><InspectorSection title="Exact control"><FactList items={[
        { label: 'Capability / rule', value: selected.id, mono: true },
        { label: 'Operation', value: selected.operation, mono: true },
        { label: 'Exact scope', value: selected.scope || '—', mono: true },
        { label: 'Revision', value: revision, mono: true },
      ]} /></InspectorSection><InspectorSection title="Current decision"><div className="authority-selector" role="group" aria-label={`${selected.scope} ${selected.operation} decision`}>{(['NO', 'YES', 'CONFIRM'] as Decision[]).map(value => <button type="button" className={`${selected.decision === value ? 'active' : ''} decision-${value.toLowerCase()}`} disabled={save.isPending} onClick={() => save.mutate({ scope: selected.scope, operation: selected.operation, decision: value })} key={value}>{value}</button>)}</div><p className="ops-authority-note"><StatusLamp tone={selected.decision === 'CONFIRM' ? 'red' : selected.decision === 'YES' ? 'amber' : 'dim'} />{selected.decision === 'CONFIRM' ? 'Confirmation is required before this action can execute.' : selected.decision === 'YES' ? 'The runtime may execute the resolved action without owner confirmation.' : 'The runtime blocks this operation at the exact matching scope.'}</p></InspectorSection></> : <div className="empty-state compact"><strong>No rule selected</strong></div>}
      {save.isError ? <p className="offline-banner">{save.error.message}</p> : null}
      <InspectorSection title="Advanced"><details className="inspect policy-advanced"><summary>Set explicit override</summary><form className="policy-add" onSubmit={submit}><input value={scope} onChange={e => setScope(e.target.value)} placeholder="resource scope" aria-label="Resource scope" /><input value={operation} onChange={e => setOperation(e.target.value)} placeholder="operation" aria-label="Operation" /><select value={decision} onChange={e => setDecision(e.target.value as Decision)} aria-label="Override decision"><option>NO</option><option>YES</option><option>CONFIRM</option></select><button className="primary" type="submit">Set override</button></form></details></InspectorSection>
    </InspectorPanel>
  </div>
}

export function Providers({ providers, onDone }: { providers: Provider[]; onDone: () => Promise<unknown> }) {
  const [selectedKey, setSelectedKey] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [key, setKey] = useState(''); const [kind, setKind] = useState('openai_compatible'); const [model, setModel] = useState(''); const [base, setBase] = useState(''); const [secret, setSecret] = useState(''); const [workspaceId, setWorkspaceId] = useState(''); const [priority, setPriority] = useState(50)
  const [replacementKeys, setReplacementKeys] = useState<Record<string, string>>({})
  const [providerPriorities, setProviderPriorities] = useState<Record<string, number>>({})
  const [providerWorkspaceIds, setProviderWorkspaceIds] = useState<Record<string, string>>({})
  const save = useMutation({ mutationFn: (payload: object) => api('/api/providers', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: async () => { setAddOpen(false); setKey(''); setModel(''); setBase(''); setSecret(''); setWorkspaceId(''); await onDone() } })
  const update = useMutation({
    mutationFn: ({ item, apiKey, enabled, priority: nextPriority, workspaceId: nextWorkspaceId }: { item: Provider; apiKey?: string; enabled?: boolean; priority?: number; workspaceId?: string }) => api('/api/providers', {
      method: 'POST',
      body: JSON.stringify({ key: item.key, kind: item.kind, model: item.model, base_url: item.base_url ?? null, api_key: apiKey || undefined, enabled: enabled ?? item.enabled, local: item.local, priority: nextPriority ?? item.priority, metadata: item.kind === 'anthropic' && nextWorkspaceId !== undefined ? { ...item.metadata, workspace_id: nextWorkspaceId.trim() || undefined } : item.metadata }),
    }),
    onSuccess: async (_data, variables) => { setReplacementKeys(current => ({ ...current, [variables.item.key]: '' })); await onDone() },
  })
  const verify = useMutation({ mutationFn: (providerKey: string) => api(`/api/providers/${providerKey}/verify`, { method: 'POST', body: '{}' }) })
  const remove = useMutation({ mutationFn: (providerKey: string) => api(`/api/providers/${providerKey}`, { method: 'DELETE' }), onSuccess: onDone })
  function submit(event: FormEvent) { event.preventDefault(); save.mutate({ key, kind, model, base_url: base || null, api_key: secret || undefined, enabled: true, local: kind === 'openai_compatible' && base.includes('127.0.0.1'), priority, metadata: kind === 'anthropic' && workspaceId.trim() ? { workspace_id: workspaceId.trim() } : undefined }) }
  const ordered = [...providers].sort((a, b) => b.priority - a.priority || a.key.localeCompare(b.key))
  const selected = ordered.find(item => item.key === selectedKey) ?? ordered[0] ?? null
  const draftPriority = selected ? providerPriorities[selected.key] ?? selected.priority : 0
  const configuredWorkspaceId = selected && typeof selected.metadata?.workspace_id === 'string' ? selected.metadata.workspace_id : ''
  const draftWorkspaceId = selected ? providerWorkspaceIds[selected.key] ?? configuredWorkspaceId : ''
  return <>
    <div className="atlas-control-grid atlas-provider-control">
      <section className="ops-rail-panel"><div className="atlas-panel-heading"><span className="eyebrow">Provider registry</span><button type="button" className="compact-button" onClick={() => setAddOpen(true)}>Add provider</button></div><p className="meta">Higher priority runs first. Failure falls through to the next enabled provider.</p><div className="atlas-registry-list">{ordered.map(item => <OperationalRow key={item.key} active={selected?.key === item.key} lamp={item.enabled ? 'green' : 'dim'} label={item.key} secondary={`${item.kind} · ${item.model}`} meta={`priority ${item.priority}`} onClick={() => setSelectedKey(item.key)} />)}{!ordered.length ? <div className="empty-state compact"><strong>No providers configured</strong></div> : null}</div></section>
      <section className="ops-surface atlas-configuration-surface"><header className="ops-surface-head"><div><span className="eyebrow">Provider configuration</span><strong>{selected?.key ?? 'Select a provider'}</strong></div>{selected ? <StatusLamp tone={selected.enabled ? 'green' : 'dim'} label={selected.enabled ? 'Enabled' : 'Disabled'} /> : null}</header>{selected ? <div className="atlas-configuration-body"><FactList items={[
        { label: 'Provider ID', value: selected.key, mono: true },
        { label: 'Adapter', value: selected.kind, mono: true },
        { label: 'Model', value: selected.model, mono: true },
        { label: 'Endpoint', value: selected.base_url || 'Provider default', mono: true },
        { label: 'Local', value: selected.local ? 'Yes' : 'No' },
        { label: 'Updated', value: when(selected.updated_at), mono: true },
      ]} /><section className="atlas-inline-control"><div><span className="eyebrow">Priority</span><strong>Provider ordering</strong></div><div className="provider-priority"><label><span>Priority</span><input type="number" aria-label={`Priority for ${selected.key}`} value={draftPriority} onChange={e => setProviderPriorities(current => ({ ...current, [selected.key]: Number(e.target.value) }))} /></label><button type="button" disabled={draftPriority === selected.priority || update.isPending} onClick={() => update.mutate({ item: selected, priority: draftPriority })}>Save</button></div></section>{selected.kind === 'anthropic' ? <section className="atlas-inline-control"><div><span className="eyebrow">Workspace</span><strong>Optional workspace-scoped key</strong></div><div className="provider-priority"><label><span>Workspace ID</span><input aria-label={`Workspace ID for ${selected.key}`} value={draftWorkspaceId} onChange={e => setProviderWorkspaceIds(current => ({ ...current, [selected.key]: e.target.value }))} placeholder="wrkspc_…" /></label><button type="button" disabled={draftWorkspaceId === configuredWorkspaceId || update.isPending} onClick={() => update.mutate({ item: selected, workspaceId: draftWorkspaceId })}>Save workspace</button></div></section> : null}<section className="atlas-credential-state"><div><StatusLamp tone={selected.credential_configured ? 'green' : 'dim'} /><span><strong>Credential {selected.credential_configured ? 'configured' : 'not configured'}</strong><small>Secret values are never returned to the browser.</small></span></div><details className="inspect provider-credential"><summary>{selected.credential_configured ? 'Replace API key' : 'Add API key'}</summary><div className="provider-key-row"><input type="password" aria-label={`Replace API key for ${selected.key}`} value={replacementKeys[selected.key] ?? ''} onChange={e => setReplacementKeys(current => ({ ...current, [selected.key]: e.target.value }))} placeholder="API key" /><button type="button" disabled={!replacementKeys[selected.key]?.trim() || update.isPending} onClick={() => update.mutate({ item: selected, apiKey: replacementKeys[selected.key].trim(), enabled: true })}>Save key & enable</button></div></details></section>{update.isError ? <p className="offline-banner">{update.error.message}</p> : null}</div> : <div className="empty-state"><strong>Select a configured provider</strong></div>}</section>
      <InspectorPanel title={selected?.key ?? 'Provider actions'} eyebrow="Provider actions" status={selected ? <StatusLamp tone={selected.enabled ? 'green' : 'dim'} label={selected.enabled ? 'Enabled' : 'Disabled'} /> : undefined} actions={selected ? <><button type="button" onClick={() => verify.mutate(selected.key)} disabled={verify.isPending}>{verify.isPending ? 'Verifying…' : 'Verify provider'}</button><button type="button" onClick={() => update.mutate({ item: selected, enabled: !selected.enabled })}>{selected.enabled ? 'Disable' : 'Enable'}</button><button type="button" className="danger" onClick={() => remove.mutate(selected.key)}>Remove provider</button></> : undefined}>
        {selected ? <><InspectorSection title="Selected provider"><FactList items={[{ label: 'Provider', value: selected.key, mono: true }, { label: 'Adapter', value: selected.kind, mono: true }, { label: 'Credential', value: selected.credential_configured ? 'Configured' : 'Not configured' }, { label: 'Priority', value: selected.priority, mono: true }]} /></InspectorSection><InspectorSection title="Verification">{verify.data && verify.variables === selected.key ? <VerificationResult result={verify.data} /> : <p className="meta">No verification result for this provider in this browser session.</p>}{verify.isError && verify.variables === selected.key ? <p className="offline-banner">{verify.error.message}</p> : null}</InspectorSection></> : <div className="empty-state compact"><strong>No provider selected</strong></div>}
      </InspectorPanel>
    </div>
    {addOpen ? <Sheet title="Add provider" onClose={() => setAddOpen(false)}><form className="atlas-sheet-form" onSubmit={submit}><input value={key} onChange={e => setKey(e.target.value)} placeholder="provider key" aria-label="Provider key" /><select value={kind} onChange={e => setKind(e.target.value)} aria-label="Provider adapter"><option value="openai_compatible">OpenAI compatible</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="gemini">Gemini</option></select><input value={model} onChange={e => setModel(e.target.value)} placeholder="model" aria-label="Model" /><input value={base} onChange={e => setBase(e.target.value)} placeholder="base URL (optional)" aria-label="Base URL" /><input type="password" value={secret} onChange={e => setSecret(e.target.value)} placeholder="API key (optional)" aria-label="API key" />{kind === 'anthropic' ? <input value={workspaceId} onChange={e => setWorkspaceId(e.target.value)} placeholder="Workspace ID (optional)" aria-label="New Anthropic workspace ID" /> : null}<input type="number" value={priority} onChange={e => setPriority(Number(e.target.value))} aria-label="New provider priority" /><button className="primary" type="submit" disabled={!key || !model || save.isPending}>{save.isPending ? 'Saving…' : 'Save provider'}</button>{save.isError ? <p className="offline-banner">{save.error.message}</p> : null}</form></Sheet> : null}
  </>
}

export function WebProviders({ providers, onDone }: { providers: WebProvider[]; onDone: () => Promise<unknown> }) {
  const [selectedKey, setSelectedKey] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [key, setKey] = useState(''); const [kind, setKind] = useState<WebProvider['kind']>('brave'); const [secret, setSecret] = useState(''); const [priority, setPriority] = useState(50)
  const [replacementKeys, setReplacementKeys] = useState<Record<string, string>>({})
  const save = useMutation({ mutationFn: (payload: object) => api('/api/web/providers', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: async () => { setAddOpen(false); setKey(''); setSecret(''); await onDone() } })
  const update = useMutation({
    mutationFn: ({ item, apiKey, enabled }: { item: WebProvider; apiKey?: string; enabled?: boolean }) => api('/api/web/providers', { method: 'POST', body: JSON.stringify({ key: item.key, kind: item.kind, api_key: apiKey || undefined, enabled: enabled ?? item.enabled, priority: item.priority, metadata: item.metadata }) }),
    onSuccess: async (_data, variables) => { setReplacementKeys(current => ({ ...current, [variables.item.key]: '' })); await onDone() },
  })
  const verify = useMutation({ mutationFn: (providerKey: string) => api(`/api/web/providers/${providerKey}/verify`, { method: 'POST', body: '{}' }) })
  const remove = useMutation({ mutationFn: (providerKey: string) => api(`/api/web/providers/${providerKey}`, { method: 'DELETE' }), onSuccess: onDone })
  function submit(event: FormEvent) { event.preventDefault(); save.mutate({ key, kind, api_key: secret, enabled: true, priority }) }
  const ordered = [...providers].sort((a, b) => b.priority - a.priority || a.key.localeCompare(b.key))
  const selected = ordered.find(item => item.key === selectedKey) ?? ordered[0] ?? null
  return <>
    <div className="atlas-control-grid atlas-provider-control">
      <section className="ops-rail-panel"><div className="atlas-panel-heading"><span className="eyebrow">Web provider registry</span><button type="button" className="compact-button" onClick={() => setAddOpen(true)}>Add web provider</button></div><p className="meta">Search providers are tried by priority. Direct public-page reading remains provider-neutral.</p><div className="atlas-registry-list">{ordered.map(item => <OperationalRow key={item.key} active={selected?.key === item.key} lamp={item.enabled && item.credential_configured ? 'green' : 'dim'} label={item.key} secondary={item.kind} meta={`priority ${item.priority}`} onClick={() => setSelectedKey(item.key)} />)}{!ordered.length ? <div className="empty-state compact"><strong>No web search provider configured</strong><span>web.read remains available; web.search reports unavailable until a credentialed provider is added.</span></div> : null}</div></section>
      <section className="ops-surface atlas-configuration-surface"><header className="ops-surface-head"><div><span className="eyebrow">Web search transport</span><strong>{selected?.key ?? 'Provider-neutral web layer'}</strong></div>{selected ? <StatusLamp tone={selected.enabled && selected.credential_configured ? 'green' : 'dim'} label={selected.enabled ? 'Enabled' : 'Disabled'} /> : null}</header>{selected ? <div className="atlas-configuration-body"><FactList items={[
        { label: 'Provider ID', value: selected.key, mono: true },
        { label: 'Adapter', value: selected.kind, mono: true },
        { label: 'Priority', value: selected.priority, mono: true },
        { label: 'Credential', value: selected.credential_configured ? 'Configured' : 'Not configured' },
        { label: 'Updated', value: when(selected.updated_at), mono: true },
      ]} /><section className="atlas-credential-state"><div><StatusLamp tone={selected.credential_configured ? 'green' : 'dim'} /><span><strong>Search credential {selected.credential_configured ? 'configured' : 'missing'}</strong><small>The key stays in Atlas encrypted credential storage and is never returned here.</small></span></div><details className="inspect provider-credential"><summary>{selected.credential_configured ? 'Replace API key' : 'Add API key'}</summary><div className="provider-key-row"><input type="password" aria-label={`Replace web API key for ${selected.key}`} value={replacementKeys[selected.key] ?? ''} onChange={e => setReplacementKeys(current => ({ ...current, [selected.key]: e.target.value }))} placeholder="Web search API key" /><button type="button" disabled={!replacementKeys[selected.key]?.trim() || update.isPending} onClick={() => update.mutate({ item: selected, apiKey: replacementKeys[selected.key].trim(), enabled: true })}>Save key & enable</button></div></details></section>{update.isError ? <p className="offline-banner">{update.error.message}</p> : null}</div> : <div className="empty-state"><strong>Add Brave, Jina, Tavily, or Serper to enable web.search.</strong><span>Stable Atlas capability names do not change when the provider changes.</span></div>}</section>
      <InspectorPanel title={selected?.key ?? 'Web provider actions'} eyebrow="Web provider actions" status={selected ? <StatusLamp tone={selected.enabled && selected.credential_configured ? 'green' : 'dim'} label={selected.enabled ? 'Enabled' : 'Disabled'} /> : undefined} actions={selected ? <><button type="button" onClick={() => verify.mutate(selected.key)} disabled={verify.isPending}>{verify.isPending ? 'Verifying…' : 'Verify web search'}</button><button type="button" onClick={() => update.mutate({ item: selected, enabled: !selected.enabled })}>{selected.enabled ? 'Disable' : 'Enable'}</button><button type="button" className="danger" onClick={() => remove.mutate(selected.key)}>Remove web provider</button></> : undefined}>
        {selected ? <><InspectorSection title="Capability boundary"><FactList items={[{ label: 'Provides', value: 'web.search', mono: true }, { label: 'Authority scope', value: 'web/search', mono: true }, { label: 'Adapter', value: selected.kind, mono: true }]} /><p className="ops-authority-note"><StatusLamp tone="amber" />Provider access makes search technically available; owner policy still decides whether Atlas may invoke it. Task-specific reasoning stays in Atlas.</p></InspectorSection><InspectorSection title="Verification">{verify.data && verify.variables === selected.key ? <VerificationResult result={verify.data} /> : <p className="meta">Verification performs one real provider search and may consume provider quota.</p>}{verify.isError && verify.variables === selected.key ? <p className="offline-banner">{verify.error.message}</p> : null}</InspectorSection></> : <InspectorSection title="Separation"><p className="ops-authority-note"><StatusLamp tone="dim" />Task-specific reasoning stays in Atlas. The provider only returns evidence and provenance.</p></InspectorSection>}
      </InspectorPanel>
    </div>
    {addOpen ? <Sheet title="Add web search provider" onClose={() => setAddOpen(false)}><form className="atlas-sheet-form" onSubmit={submit}><input value={key} onChange={e => setKey(e.target.value)} placeholder="provider key" aria-label="Web provider key" /><select value={kind} onChange={e => setKind(e.target.value as WebProvider['kind'])} aria-label="Web provider adapter"><option value="brave">Brave Search</option><option value="jina">Jina Search</option><option value="tavily">Tavily</option><option value="serper">Serper</option></select><input type="password" value={secret} onChange={e => setSecret(e.target.value)} placeholder="API key" aria-label="Web provider API key" /><input type="number" value={priority} onChange={e => setPriority(Number(e.target.value))} aria-label="Web provider priority" /><button className="primary" type="submit" disabled={!key || !secret || save.isPending}>{save.isPending ? 'Saving…' : 'Save web provider'}</button>{save.isError ? <p className="offline-banner">{save.error.message}</p> : null}</form></Sheet> : null}
  </>
}

function Mcp({ servers, capabilities, connections, bindings, onDone }: { servers: MCPServer[]; capabilities: Capability[]; connections: Array<Record<string, unknown>>; bindings: Array<Record<string, unknown>>; onDone: () => Promise<unknown> }) {
  const location = useLocation()
  const parentBack = (location.state as { atlasBack?: AtlasBack } | null)?.atlasBack
  const [mode, setMode] = useState<'services' | 'accounts' | 'bindings'>('services')
  const [selectedId, setSelectedId] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [serverId, setServerId] = useState(''); const [name, setName] = useState(''); const [kind, setKind] = useState<'mcp' | 'n8n'>('mcp'); const [transport, setTransport] = useState<'streamable-http' | 'stdio'>('streamable-http'); const [url, setUrl] = useState(''); const [token, setToken] = useState(''); const [command, setCommand] = useState(''); const [argsText, setArgsText] = useState(''); const [cwd, setCwd] = useState('')
  const save = useMutation({ mutationFn: (payload: object) => api('/api/mcp', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: async () => { setAddOpen(false); setServerId(''); setName(''); setUrl(''); setToken(''); setCommand(''); setArgsText(''); setCwd(''); await onDone() } })
  const refresh = useMutation({ mutationFn: (id: string) => api(`/api/mcp/${id}/refresh`, { method: 'POST', body: '{}' }), onSuccess: onDone })
  const remove = useMutation({ mutationFn: (id: string) => api(`/api/mcp/${id}`, { method: 'DELETE' }), onSuccess: onDone })
  function submit(event: FormEvent) { event.preventDefault(); save.mutate({ server_id: serverId, display_name: name || serverId, kind, transport, url: transport === 'streamable-http' ? url : null, token: transport === 'streamable-http' ? token || undefined : undefined, command: transport === 'stdio' ? command : null, args: transport === 'stdio' ? argsText.split('\n').map(item => item.trim()).filter(Boolean) : [], cwd: transport === 'stdio' ? cwd || null : null, enabled: true }) }
  const selected = servers.find(server => server.server_id === selectedId) ?? servers[0] ?? null
  const tools = selected ? capabilities.filter(item => item.metadata?.server_id === selected.server_id) : []
  const externalRows = mode === 'accounts' ? connections : bindings
  const externalTitle = mode === 'accounts' ? 'External accounts' : 'Technical service bindings'
  const externalFacts = (item: Record<string, unknown>) => mode === 'accounts' ? [
    { label: 'Connection ID', value: String(item.connection_id ?? '—'), mono: true },
    { label: 'Provider', value: String(item.provider_id ?? '—'), mono: true },
    { label: 'Address', value: String(item.canonical_address ?? '—'), mono: true },
    { label: 'Status', value: String(item.status ?? '—') },
  ] : [
    { label: 'Binding ID', value: String(item.binding_id ?? '—'), mono: true },
    { label: 'Service', value: String(item.service ?? '—'), mono: true },
    { label: 'Channel', value: String(item.channel ?? '—'), mono: true },
    { label: 'Attested operations', value: Array.isArray(item.attested_operations) ? item.attested_operations.join(', ') : '—', mono: true },
  ]
  return <>
    <div className="atlas-control-grid atlas-connection-control">
      <section className="ops-rail-panel"><div className="atlas-mode-switch" role="tablist" aria-label="Connection registry"><button type="button" role="tab" aria-selected={mode === 'services'} className={mode === 'services' ? 'active' : ''} onClick={() => setMode('services')}>Services</button><button type="button" role="tab" aria-selected={mode === 'accounts'} className={mode === 'accounts' ? 'active' : ''} onClick={() => setMode('accounts')}>Accounts</button><button type="button" role="tab" aria-selected={mode === 'bindings'} className={mode === 'bindings' ? 'active' : ''} onClick={() => setMode('bindings')}>Bindings</button></div>{mode === 'services' ? <><div className="atlas-panel-heading"><span className="eyebrow">Connection registry</span><button type="button" className="compact-button" onClick={() => setAddOpen(true)}>Add service</button></div><div className="atlas-registry-list">{servers.map(item => <OperationalRow key={item.server_id} active={selected?.server_id === item.server_id} lamp={item.last_error ? 'red' : item.enabled ? 'green' : 'dim'} label={item.display_name} secondary={`${item.kind} · ${item.transport}`} meta={`${item.discovered_tool_count} tools`} onClick={() => setSelectedId(item.server_id)} />)}{!servers.length ? <div className="empty-state compact"><strong>No services configured</strong></div> : null}</div></> : <><div className="atlas-panel-heading"><span className="eyebrow">{externalTitle}</span><span className="chip">{externalRows.length}</span></div><p className="meta">These are technical custody records, not an authority model.</p></>}</section>
      <section className="ops-surface atlas-configuration-surface">{mode === 'services' ? <><header className="ops-surface-head"><div><span className="eyebrow">Service configuration</span><strong>{selected?.display_name ?? 'Select a service'}</strong></div>{selected ? <StatusLamp tone={selected.last_error ? 'red' : selected.enabled ? 'green' : 'dim'} label={selected.last_error ? 'Discovery error' : selected.enabled ? 'Enabled' : 'Disabled'} /> : null}</header>{selected ? <div className="atlas-configuration-body"><FactList items={[
        { label: 'Server ID', value: selected.server_id, mono: true },
        { label: 'Kind', value: selected.kind, mono: true },
        { label: 'Transport', value: selected.transport, mono: true },
        { label: 'Endpoint', value: selected.transport === 'stdio' ? selected.command ?? '—' : selected.url ?? '—', mono: true },
        { label: 'Enabled', value: selected.enabled ? 'Yes' : 'No' },
        { label: 'Credential configured', value: selected.credential_configured ? 'Yes' : 'No' },
        { label: 'Last discovery', value: when(selected.last_discovered_at), mono: true },
      ]} />{selected.last_error ? <p className="offline-banner">{selected.last_error}</p> : null}<section className="atlas-discovered-tools"><div className="atlas-panel-heading"><span className="eyebrow">Discovered tools</span><span className="mono meta">{tools.length} inventory items</span></div><div className="atlas-capability-rows">{tools.slice(0, 12).map(item => <OperationalRow key={item.id} lamp={item.available ? 'green' : 'dim'} label={String(item.metadata?.tool_name ?? item.id)} secondary={item.description} status={<span className="meta">{item.available ? 'available' : 'unavailable'}</span>} />)}{!tools.length ? <div className="empty-state compact"><strong>No tools in the current capability snapshot.</strong></div> : null}</div></section></div> : <div className="empty-state"><strong>Select a configured service</strong></div>}</> : <><header className="ops-surface-head"><div><span className="eyebrow">{externalTitle}</span><strong>{externalRows.length} runtime records</strong></div></header><div className="atlas-external-records">{externalRows.map((item, index) => <article className="atlas-external-record" key={String(item.connection_id ?? item.binding_id ?? index)}><strong>{String(item.display_name ?? item.canonical_address ?? item.service ?? item.connection_id ?? item.binding_id ?? `Record ${index + 1}`)}</strong><FactList items={externalFacts(item)} /></article>)}{!externalRows.length ? <div className="empty-state"><strong>No {mode} are present in the runtime snapshot.</strong></div> : null}</div></>}</section>
      <InspectorPanel title={mode === 'services' ? selected?.display_name ?? 'Service actions' : externalTitle} eyebrow={mode === 'services' ? 'Service actions' : 'Technical custody'} status={mode === 'services' && selected ? <StatusLamp tone={selected.last_error ? 'red' : selected.enabled ? 'green' : 'dim'} label={selected.last_error ? 'Error' : selected.enabled ? 'Enabled' : 'Disabled'} /> : undefined} actions={mode === 'services' && selected ? <><button type="button" disabled={refresh.isPending} onClick={() => refresh.mutate(selected.server_id)}>{refresh.isPending ? 'Refreshing…' : 'Refresh discovery'}</button><Link className="button-link" to="/atlas/policies" state={{ atlasBack: { path: location.pathname, parent: parentBack ?? { path: '/atlas' } } }}>Open policy rules</Link><button type="button" className="danger" onClick={() => remove.mutate(selected.server_id)}>Remove service</button></> : undefined}>
        {mode === 'services' && selected ? <><InspectorSection title="Discovery"><FactList items={[{ label: 'Tools discovered', value: selected.discovered_tool_count }, { label: 'Last discovery', value: when(selected.last_discovered_at), mono: true }, { label: 'Last error', value: selected.last_error ?? 'None reported' }]} /></InspectorSection><InspectorSection title="Authority"><p className="ops-authority-note"><StatusLamp tone="amber" />Discovery populates capability inventory only. NO / YES / CONFIRM remains under owner policy.</p></InspectorSection>{refresh.isError ? <p className="offline-banner">{refresh.error.message}</p> : null}</> : <InspectorSection title="Boundary"><p className="ops-authority-note"><StatusLamp tone="dim" />Connections and bindings describe technical custody. They do not grant action authority.</p></InspectorSection>}
      </InspectorPanel>
    </div>
    {addOpen ? <Sheet title="Add service" onClose={() => setAddOpen(false)}><form className="atlas-sheet-form" onSubmit={submit}><input value={serverId} onChange={e => setServerId(e.target.value)} placeholder="server id" aria-label="Server ID" /><input value={name} onChange={e => setName(e.target.value)} placeholder="display name" aria-label="Display name" /><select value={kind} onChange={e => setKind(e.target.value as 'mcp' | 'n8n')} aria-label="Connection kind"><option value="mcp">MCP</option><option value="n8n">n8n</option></select><select value={transport} onChange={e => setTransport(e.target.value as 'streamable-http' | 'stdio')} aria-label="Transport"><option value="streamable-http">Streamable HTTP</option><option value="stdio">stdio</option></select>{transport === 'streamable-http' ? <><input value={url} onChange={e => setUrl(e.target.value)} placeholder="Streamable HTTP URL" aria-label="Streamable HTTP URL" /><input type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="Bearer token (optional)" aria-label="Bearer token" /></> : <><input value={command} onChange={e => setCommand(e.target.value)} placeholder="command / executable" aria-label="Command" /><textarea value={argsText} onChange={e => setArgsText(e.target.value)} placeholder="one argument per line" aria-label="Arguments" /><input value={cwd} onChange={e => setCwd(e.target.value)} placeholder="working directory (optional)" aria-label="Working directory" /></>}<button className="primary" type="submit" disabled={!serverId || save.isPending}>{save.isPending ? 'Saving…' : 'Save service'}</button>{save.isError ? <p className="offline-banner">{save.error.message}</p> : null}</form></Sheet> : null}
  </>
}


function Roots({ roots, capabilities, onDone }: { roots: SourceRoot[]; capabilities: Capability[]; onDone: () => Promise<unknown> }) {
  const [selectedId, setSelectedId] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [id, setId] = useState(''); const [path, setPath] = useState(''); const [name, setName] = useState('')
  const save = useMutation({
    mutationFn: () => api('/api/sources/roots', { method: 'POST', body: JSON.stringify({ root_id: id, host_path: path, display_name: name || id, enabled: true, quarantine_relative_path: '.atlas-quarantine' }) }),
    onSuccess: async () => { setAddOpen(false); await onDone(); setId(''); setName(''); setPath('') },
  })
  const remove = useMutation({ mutationFn: (rootId: string) => api(`/api/sources/roots/${rootId}`, { method: 'DELETE' }), onSuccess: onDone })
  const selected = roots.find(root => root.root_id === selectedId) ?? roots[0] ?? null
  const filesystemCapabilities = capabilities.filter(item => item.source === 'filesystem' || item.id.includes('filesystem'))
  return <>
    <div className="atlas-control-grid atlas-filesystem-control">
      <section className="ops-rail-panel"><div className="atlas-panel-heading"><span className="eyebrow">Root registry</span><button type="button" className="compact-button" onClick={() => setAddOpen(true)}>Enroll root</button></div><div className="atlas-registry-list">{roots.map(root => <OperationalRow key={root.root_id} active={selected?.root_id === root.root_id} lamp={root.enabled ? 'green' : 'dim'} label={root.display_name} secondary={<span className="mono">{root.host_path}</span>} meta={root.provider_namespace} onClick={() => setSelectedId(root.root_id)} />)}{!roots.length ? <div className="empty-state compact"><strong>No roots enrolled</strong></div> : null}</div></section>
      <section className="ops-surface atlas-configuration-surface"><header className="ops-surface-head"><div><span className="eyebrow">Root configuration</span><strong>{selected?.display_name ?? 'Select a root'}</strong></div>{selected ? <StatusLamp tone={selected.enabled ? 'green' : 'dim'} label={selected.enabled ? 'Enabled' : 'Disabled'} /> : null}</header>{selected ? <div className="atlas-configuration-body"><FactList items={[
        { label: 'Root ID', value: selected.root_id, mono: true },
        { label: 'Provider namespace', value: selected.provider_namespace, mono: true },
        { label: 'Host path', value: selected.host_path, mono: true },
        { label: 'Quarantine relative path', value: selected.quarantine_relative_path ?? 'Not configured', mono: true },
        { label: 'Enabled', value: selected.enabled ? 'Yes' : 'No' },
        { label: 'Updated', value: when(selected.updated_at), mono: true },
      ]} /><p className="atlas-boundary-notice"><StatusLamp tone="amber" /><span><strong>Capability is not authority.</strong> Enrollment exposes a technical boundary. Owner policy still resolves every consequential operation.</span></p><section className="atlas-discovered-tools"><div className="atlas-panel-heading"><span className="eyebrow">Runtime filesystem capabilities</span><span className="mono meta">{filesystemCapabilities.length} inventory items</span></div><div className="atlas-capability-rows">{filesystemCapabilities.map(item => <OperationalRow key={item.id} lamp={item.available ? 'green' : 'dim'} label={item.id} secondary={item.description} status={<span className="meta">{item.available ? 'available' : 'unavailable'}</span>} />)}{!filesystemCapabilities.length ? <div className="empty-state compact"><strong>No filesystem capabilities in the current runtime snapshot.</strong></div> : null}</div></section></div> : <div className="empty-state"><strong>Select an enrolled root</strong></div>}</section>
      <InspectorPanel title={selected?.display_name ?? 'Root actions'} eyebrow="Root actions" status={selected ? <StatusLamp tone={selected.enabled ? 'green' : 'dim'} label={selected.enabled ? 'Enabled' : 'Disabled'} /> : undefined} actions={selected ? <><Link className="button-link" to="/atlas/policies">Open policy rules</Link><Link className="button-link" to="/sources">Browse source</Link>{selected.provider_namespace !== 'atlas-library' ? <button type="button" className="danger" onClick={() => remove.mutate(selected.root_id)}>Remove root</button> : null}</> : undefined}>
        {selected ? <><InspectorSection title="Selected root"><FactList items={[{ label: 'Root', value: selected.root_id, mono: true }, { label: 'Namespace', value: selected.provider_namespace, mono: true }, { label: 'Path', value: selected.host_path, mono: true }]} /></InspectorSection><InspectorSection title="Authority boundary"><p className="ops-authority-note"><StatusLamp tone="amber" />Enrollment does not mean readable, writable, trusted, or authorized. Policy resolves the normalized resource when an operation runs.</p></InspectorSection>{remove.isError ? <p className="offline-banner">Remove failed: {remove.error.message}</p> : null}</> : <div className="empty-state compact"><strong>No root selected</strong></div>}
      </InspectorPanel>
    </div>
    {addOpen ? <Sheet title="Enroll root" onClose={() => setAddOpen(false)}><form className="atlas-sheet-form" onSubmit={event => { event.preventDefault(); save.mutate() }}><input value={id} onChange={e => setId(e.target.value)} placeholder="root id" aria-label="Root ID" /><input value={name} onChange={e => setName(e.target.value)} placeholder="display name" aria-label="Display name" /><input value={path} onChange={e => setPath(e.target.value)} placeholder="absolute host path" aria-label="Absolute host path" /><p className="ops-authority-note"><StatusLamp tone="amber" />This enrolls a technical filesystem boundary only. It does not grant read or mutation authority.</p><button className="primary" type="submit" disabled={!id || !path || save.isPending}>{save.isPending ? 'Enrolling…' : 'Enroll root'}</button>{save.isError ? <p className="offline-banner">Enrollment failed: {save.error.message}</p> : null}</form></Sheet> : null}
  </>
}
