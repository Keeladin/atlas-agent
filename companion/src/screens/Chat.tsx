import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { ActionOccurrence, ChatTurn, Conversation } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { Workspace, WorkspaceRailSection } from '../ui/Workspace'

type ConversationList = { conversations: Conversation[] }
type SendRequest = { conversationId: string; text: string }

export function Chat() {
  const qc = useQueryClient()
  const conversations = useQuery({ queryKey: ['conversations'], queryFn: () => api<ConversationList>('/api/chat/conversations') })
  const [selected, setSelected] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const threadEndRef = useRef<HTMLDivElement | null>(null)

  const { mutate: createConversation, isPending: createPending } = useMutation({
    mutationFn: () => api<Conversation>('/api/chat/conversations', { method: 'POST', body: JSON.stringify({ title: 'Conversation' }) }),
    onSuccess: item => {
      qc.setQueryData<ConversationList>(['conversations'], current => ({
        conversations: [item, ...(current?.conversations ?? []).filter(row => row.conversation_id !== item.conversation_id)],
      }))
      setSelected(item.conversation_id)
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
      await qc.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const items = useMemo(() => conversations.data?.conversations ?? [], [conversations.data])
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
  const pendingAction = useMemo(() => {
    const turns = detail.data?.turns ?? []
    const meta = turns.at(-1)?.metadata
    const embedded = meta?.requires_confirmation && meta.action && typeof meta.action === 'object' ? meta.action as ActionOccurrence : null
    if (!embedded) return null
    if (!pending.data) return embedded
    return pending.data.actions.find(action => action.occurrence_id === embedded.occurrence_id) ?? null
  }, [detail.data, pending.data])
  useEffect(() => {
    const node = threadEndRef.current
    if (node && typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'end' })
  }, [detail.data?.turns.length, pendingAction])

  function submit(event: FormEvent) {
    event.preventDefault()
    const text = message.trim()
    if (text && selected && selectedValid && !send.isPending) send.mutate({ conversationId: selected, text })
  }
  function removeConversation(item: Conversation) {
    if (window.confirm(`Delete conversation “${item.title}”? This removes its chat history.`)) deleteConversation.mutate(item.conversation_id)
  }
  const rail = <><WorkspaceRailSection title="Conversations" actions={<button type="button" onClick={() => createConversation()}>New</button>}><div className="chat-conversation-list">{items.map(item => <div className="chat-recent-wrap" key={item.conversation_id}><button type="button" className={`chat-recent-item ${selected === item.conversation_id ? 'active' : ''}`} onClick={() => setSelected(item.conversation_id)}><strong>{item.title}</strong><div className="meta">{item.updated_at}</div></button><button type="button" className="chat-recent-delete" aria-label={`Delete ${item.title}`} onClick={() => removeConversation(item)}>×</button></div>)}</div></WorkspaceRailSection></>
  return <Workspace title="Chat" subtitle="Atlas, using the live runtime capability inventory." rail={rail} railLabel="Conversations" fillHeight>
    <div className="chat-thread-wrap">
      <div className="chat-thread-body">
        {(detail.data?.turns ?? []).map(turn => <article key={turn.turn_id} className={`chat-turn ${turn.role}`}><div className="chat-turn-role">{turn.role === 'user' ? 'You' : 'Atlas'}</div><div className="chat-turn-content">{turn.content}</div></article>)}
        {pendingAction ? <ConfirmationCard item={pendingAction as never} onDone={async () => { await qc.invalidateQueries({ queryKey: ['conversation', selected] }); await qc.invalidateQueries({ queryKey: ['pending-actions'] }) }} /> : null}
        <div ref={threadEndRef} aria-hidden />
      </div>
      {send.isError ? <p className="offline-banner">{send.error.message}</p> : null}
      <form className="composer" onSubmit={submit}><textarea value={message} onChange={e => setMessage(e.target.value)} placeholder="Ask Atlas…" /><button className="primary" type="submit" disabled={send.isPending || !message.trim() || !selectedValid}>{send.isPending ? 'Working…' : 'Send'}</button></form>
    </div>
  </Workspace>
}
