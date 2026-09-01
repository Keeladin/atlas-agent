import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { ActionOccurrence, ChatTurn, Conversation } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { FactList, InspectorPanel, InspectorSection, OperationalRibbon, StatusLamp } from '../ui/OperationsPrimitives'
import type { LampTone } from '../ui/operationState'
import { Workspace, WorkspaceRailSection } from '../ui/Workspace'

type ConversationList = { conversations: Conversation[] }
type SendRequest = { conversationId: string; text: string }

function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const today = new Date()
  const sameDay = date.getFullYear() === today.getFullYear() && date.getMonth() === today.getMonth() && date.getDate() === today.getDate()
  return sameDay ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function actionForTurn(turn?: ChatTurn | null) {
  const value = turn?.metadata?.action
  return value && typeof value === 'object' ? value as ActionOccurrence : null
}

function toolsForTurn(turn?: ChatTurn | null) {
  const tools = Array.isArray(turn?.metadata?.tools_used) ? turn.metadata.tools_used.filter((value): value is string => typeof value === 'string' && Boolean(value)) : []
  const action = actionForTurn(turn)
  return [...new Set([...tools, ...(action?.capability_id ? [action.capability_id] : [])])]
}

function actionTone(status?: string | null): LampTone {
  if (status === 'succeeded') return 'green'
  if (status === 'pending_confirmation' || status === 'executing' || status === 'uncertain') return 'amber'
  if (status === 'failed' || status === 'blocked' || status === 'expired' || status === 'cancelled') return 'red'
  return 'dim'
}

export function Chat() {
  const qc = useQueryClient()
  const conversations = useQuery({ queryKey: ['conversations'], queryFn: () => api<ConversationList>('/api/chat/conversations') })
  const [selected, setSelected] = useState<string | null>(null)
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null)
  const [conversationFilter, setConversationFilter] = useState('')
  const [message, setMessage] = useState('')
  const threadEndRef = useRef<HTMLDivElement | null>(null)

  const { mutate: createConversation, isPending: createPending } = useMutation({
    mutationFn: () => api<Conversation>('/api/chat/conversations', { method: 'POST', body: JSON.stringify({ title: 'Conversation' }) }),
    onSuccess: item => {
      qc.setQueryData<ConversationList>(['conversations'], current => ({
        conversations: [item, ...(current?.conversations ?? []).filter(row => row.conversation_id !== item.conversation_id)],
      }))
      setSelected(item.conversation_id)
      setSelectedTurnId(null)
      void qc.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
  const deleteConversation = useMutation({
    mutationFn: (conversationId: string) => api(`/api/chat/conversations/${conversationId}`, { method: 'DELETE' }),
    onSuccess: async (_data, conversationId) => {
      const current = qc.getQueryData<ConversationList>(['conversations'])?.conversations ?? []
      const remaining = current.filter(item => item.conversation_id !== conversationId)
      qc.setQueryData<ConversationList>(['conversations'], { conversations: remaining })
      qc.removeQueries({ queryKey: ['conversation', conversationId] })
      setSelected(value => value === conversationId ? (remaining[0]?.conversation_id ?? null) : value)
      setSelectedTurnId(null)
      await qc.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const items = useMemo(() => conversations.data?.conversations ?? [], [conversations.data])
  const visibleConversations = useMemo(() => items.filter(item => !conversationFilter || item.title.toLowerCase().includes(conversationFilter.toLowerCase())), [items, conversationFilter])
  useEffect(() => {
    if (selected && items.some(item => item.conversation_id === selected)) return
    setSelected(items[0]?.conversation_id ?? null)
  }, [items, selected])
  useEffect(() => {
    if (!selected && conversations.isSuccess && items.length === 0 && !createPending) createConversation()
  }, [selected, conversations.isSuccess, items.length, createPending, createConversation])

  const selectedValid = Boolean(selected && items.some(item => item.conversation_id === selected))
  const detail = useQuery({
    queryKey: ['conversation', selected],
    queryFn: () => api<{ conversation: Conversation; turns: ChatTurn[] }>(`/api/chat/conversations/${selected}`),
    enabled: selectedValid,
  })
  const pending = useQuery({ queryKey: ['pending-actions'], queryFn: () => api<{ actions: ActionOccurrence[] }>('/api/actions/pending'), refetchInterval: 5000 })
  const send = useMutation({
    mutationFn: ({ conversationId, text }: SendRequest) => api<{ turn: ChatTurn; action?: Record<string, unknown> }>(`/api/chat/conversations/${conversationId}/messages`, { method: 'POST', body: JSON.stringify({ message: text }) }),
    onSuccess: async (_data, variables) => {
      setMessage('')
      await qc.invalidateQueries({ queryKey: ['conversation', variables.conversationId] })
      await qc.invalidateQueries({ queryKey: ['pending-actions'] })
      await qc.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const turns = useMemo(() => detail.data?.turns ?? [], [detail.data?.turns])
  const pendingById = useMemo(() => new Map((pending.data?.actions ?? []).map(action => [action.occurrence_id, action])), [pending.data?.actions])
  const conversationPending = useMemo(() => turns.flatMap(turn => {
    const recorded = actionForTurn(turn)
    const live = recorded ? pendingById.get(recorded.occurrence_id) : null
    return live ? [{ turnId: turn.turn_id, action: live }] : []
  }), [turns, pendingById])
  useEffect(() => {
    if (selectedTurnId && turns.some(turn => turn.turn_id === selectedTurnId)) return
    const latestAssistant = [...turns].reverse().find(turn => turn.role === 'assistant')
    setSelectedTurnId(latestAssistant?.turn_id ?? turns.at(-1)?.turn_id ?? null)
  }, [turns, selectedTurnId])
  useEffect(() => {
    const node = threadEndRef.current
    if (node && typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'end' })
  }, [turns.length, conversationPending.length, send.isPending])

  function submit(event: FormEvent) {
    event.preventDefault()
    const text = message.trim()
    if (text && selected && selectedValid && !send.isPending) send.mutate({ conversationId: selected, text })
  }
  function removeConversation(item: Conversation) {
    if (window.confirm(`Delete conversation “${item.title}”? This removes its chat history.`)) deleteConversation.mutate(item.conversation_id)
  }

  const currentConversation = detail.data?.conversation ?? items.find(item => item.conversation_id === selected) ?? null
  const selectedTurn = turns.find(turn => turn.turn_id === selectedTurnId) ?? null
  const selectedRecordedAction = actionForTurn(selectedTurn)
  const selectedLiveAction = selectedRecordedAction ? pendingById.get(selectedRecordedAction.occurrence_id) ?? null : null
  const selectedTools = toolsForTurn(selectedTurn)
  const selectedActionStatus = selectedLiveAction?.status ?? (selectedRecordedAction?.status === 'pending_confirmation' ? 'not pending' : selectedRecordedAction?.status)
  const selectedError = typeof selectedTurn?.metadata?.error === 'string' ? selectedTurn.metadata.error : null

  const rail = <section className="ops-rail-panel chat-rail-panel"><WorkspaceRailSection title="Conversations" actions={<button type="button" className="compact-button" onClick={() => createConversation()} disabled={createPending}>{createPending ? 'Creating…' : 'New'}</button>}><input aria-label="Search conversations" value={conversationFilter} onChange={event => setConversationFilter(event.target.value)} placeholder="Search conversations" /><div className="chat-conversation-list">{visibleConversations.map(item => <div className="chat-recent-wrap" key={item.conversation_id}><button type="button" aria-label={`${item.title} ${item.updated_at}`} className={`chat-recent-item ${selected === item.conversation_id ? 'active' : ''}`} onClick={() => { setSelected(item.conversation_id); setSelectedTurnId(null) }}><strong>{item.title}</strong><div className="meta">{when(item.updated_at)}</div></button><button type="button" className="chat-recent-delete" title="Delete conversation" aria-label={`Delete ${item.title}`} onClick={() => removeConversation(item)}>•••</button></div>)}{!visibleConversations.length ? <div className="empty-state compact"><strong>No matching conversations</strong></div> : null}</div></WorkspaceRailSection></section>

  const context = <InspectorPanel title="Turn context" eyebrow={selectedTurn ? `Selected ${selectedTurn.role} turn` : 'Select a turn'} status={selectedTurn ? <StatusLamp tone={selectedLiveAction ? 'red' : actionTone(selectedActionStatus)} label={selectedLiveAction ? 'Pending confirmation' : selectedActionStatus?.replaceAll('_', ' ') ?? 'Recorded'} /> : undefined}>
    {selectedTurn ? <><InspectorSection title="Turn"><FactList items={[
      { label: 'Role', value: selectedTurn.role },
      { label: 'Turn ID', value: selectedTurn.turn_id, mono: true },
      { label: 'Created', value: when(selectedTurn.created_at), mono: true },
    ]} /></InspectorSection><InspectorSection title="Capabilities used">{selectedTools.length ? <div className="chat-context-tools">{selectedTools.map(tool => <div key={tool}><StatusLamp tone={selectedRecordedAction?.capability_id === tool ? (selectedLiveAction ? 'red' : actionTone(selectedActionStatus)) : 'dim'} /><span className="mono">{tool}</span></div>)}</div> : <p className="meta">No capability action was recorded for this turn.</p>}</InspectorSection>{selectedRecordedAction ? <><InspectorSection title="Action occurrence"><FactList items={[
      { label: 'Occurrence ID', value: selectedRecordedAction.occurrence_id, mono: true },
      { label: 'Status', value: selectedActionStatus?.replaceAll('_', ' ') ?? '—' },
      { label: 'Operation', value: selectedRecordedAction.operation, mono: true },
      { label: 'Scope', value: selectedRecordedAction.scope, mono: true },
    ]} /></InspectorSection><InspectorSection title="Authority"><FactList items={[
      { label: 'Decision', value: selectedRecordedAction.policy_decision, mono: true },
      { label: 'Revision', value: selectedRecordedAction.policy_revision, mono: true },
    ]} />{selectedRecordedAction.status === 'pending_confirmation' && !selectedLiveAction ? <p className="meta">This recorded action is no longer present in the live pending queue.</p> : null}</InspectorSection></> : null}{selectedError ? <p className="offline-banner">{selectedError}</p> : null}<InspectorSection title="Technical evidence"><details className="inspect chat-turn-evidence"><summary>Recorded turn metadata</summary><pre>{JSON.stringify(selectedTurn.metadata, null, 2)}</pre></details></InspectorSection></> : <div className="empty-state compact"><strong>Select a transcript turn</strong><span>Recorded capability and action context will appear here.</span></div>}
  </InspectorPanel>

  return <Workspace className="chat-control-workspace" title="Chat" subtitle="Atlas, using the live runtime capability inventory." rail={rail} railLabel="Conversations" context={context} contextLabel="Turn context" fillHeight banner={<OperationalRibbon items={[
    { label: 'Selected conversation', value: currentConversation?.title ?? '—' },
    { label: 'Turns', value: turns.length },
    { label: 'Selected turn capabilities', value: selectedTools.length },
    { label: 'Confirmations pending', value: conversationPending.length, tone: conversationPending.length ? 'red' : 'dim' },
    { label: 'Send state', value: send.isPending ? 'Working' : 'Ready', tone: send.isPending ? 'amber' : selectedValid ? 'green' : 'dim' },
  ]} />}>
    <section className="ops-surface chat-conversation-surface">
      <header className="ops-surface-head chat-conversation-head"><div><span className="eyebrow">Active conversation</span><strong>{currentConversation?.title ?? 'Conversation'}</strong><p>{turns.length} turns · Updated {when(currentConversation?.updated_at)}</p></div></header>
      <div className="chat-thread-body">{turns.map(turn => {
        const recorded = actionForTurn(turn)
        const liveAction = recorded ? pendingById.get(recorded.occurrence_id) ?? null : null
        const tools = toolsForTurn(turn)
        return <article key={turn.turn_id} className={`chat-turn ${turn.role} ${selectedTurn?.turn_id === turn.turn_id ? 'selected' : ''}`}><button type="button" className="chat-turn-select" onClick={() => setSelectedTurnId(turn.turn_id)}><span className="chat-turn-heading"><span className="chat-turn-role">{turn.role === 'user' ? 'You' : turn.role === 'assistant' ? 'Atlas' : turn.role}</span><time>{when(turn.created_at)}</time></span><span className="chat-turn-content">{turn.content}</span>{tools.length ? <span className="chat-turn-tools">{tools.map(tool => <span className="chip" key={tool}>{tool}</span>)}</span> : null}</button>{liveAction ? <div className="chat-turn-confirmation"><ConfirmationCard item={liveAction} title="Exact action confirmation" confirmLabel="Confirm exact action" cancelLabel="Reject" onDone={async () => { await qc.invalidateQueries({ queryKey: ['conversation', selected] }); await qc.invalidateQueries({ queryKey: ['pending-actions'] }) }} /></div> : null}</article>
      })}{send.isPending ? <div className="chat-working"><StatusLamp tone="amber" label="Atlas is working" /></div> : null}{detail.isError ? <p className="offline-banner">{detail.error.message}</p> : null}<div ref={threadEndRef} aria-hidden /></div>
      {send.isError ? <p className="offline-banner chat-send-error">{send.error.message}</p> : null}
      <form className="composer chat-composer" onSubmit={submit}><textarea aria-label="Message" value={message} onChange={e => setMessage(e.target.value)} placeholder="Ask Atlas…" /><div className="chat-composer-footer"><StatusLamp tone={send.isPending ? 'amber' : selectedValid ? 'green' : 'dim'} label={send.isPending ? 'Working' : selectedValid ? 'Ready' : 'Unavailable'} /><button className="primary" type="submit" disabled={send.isPending || !message.trim() || !selectedValid}>{send.isPending ? 'Working…' : 'Send'}</button></div></form>
    </section>
  </Workspace>
}
