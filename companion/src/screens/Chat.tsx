import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { ChatTurn, Conversation } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { Workspace, WorkspaceRailSection } from '../ui/Workspace'

export function Chat() {
  const qc = useQueryClient()
  const conversations = useQuery({ queryKey: ['conversations'], queryFn: () => api<{ conversations: Conversation[] }>('/api/chat/conversations') })
  const [selected, setSelected] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const { mutate: createConversation, isPending: createPending } = useMutation({ mutationFn: () => api<Conversation>('/api/chat/conversations', { method: 'POST', body: JSON.stringify({ title: 'Conversation' }) }), onSuccess: item => { setSelected(item.conversation_id); void qc.invalidateQueries({ queryKey: ['conversations'] }) } })
  useEffect(() => { if (!selected && conversations.data?.conversations.length) setSelected(conversations.data.conversations[0].conversation_id) }, [selected, conversations.data])
  useEffect(() => { if (!selected && conversations.isSuccess && !conversations.data.conversations.length && !createPending) createConversation() }, [selected, conversations.isSuccess, conversations.data, createPending, createConversation])
  const detail = useQuery({ queryKey: ['conversation', selected], queryFn: () => api<{ conversation: Conversation; turns: ChatTurn[] }>(`/api/chat/conversations/${selected}`), enabled: Boolean(selected) })
  const send = useMutation({ mutationFn: (text: string) => api<{ turn: ChatTurn; action?: Record<string, unknown> }>(`/api/chat/conversations/${selected}/messages`, { method: 'POST', body: JSON.stringify({ message: text }) }), onSuccess: async () => { setMessage(''); await qc.invalidateQueries({ queryKey: ['conversation', selected] }); await qc.invalidateQueries({ queryKey: ['pending-actions'] }); await qc.invalidateQueries({ queryKey: ['conversations'] }) } })
  const pendingAction = useMemo(() => {
    const turns = detail.data?.turns ?? []
    const meta = turns.at(-1)?.metadata
    return meta?.requires_confirmation && meta.action && typeof meta.action === 'object' ? meta.action : null
  }, [detail.data])
  function submit(event: FormEvent) { event.preventDefault(); const text = message.trim(); if (text && selected && !send.isPending) send.mutate(text) }
  const rail = <><WorkspaceRailSection title="Conversations" actions={<button type="button" onClick={() => createConversation()}>New</button>}><div className="chat-recent-wrap">{(conversations.data?.conversations ?? []).map(item => <button type="button" key={item.conversation_id} className="chat-recent-item" onClick={() => setSelected(item.conversation_id)}><strong>{item.title}</strong><div className="meta">{item.updated_at}</div></button>)}</div></WorkspaceRailSection></>
  return <Workspace title="Chat" subtitle="Atlas, using the live runtime capability inventory." rail={rail} railLabel="Conversations" fillHeight>
    <div className="chat-thread-wrap">
      <div className="chat-thread-body">
        {(detail.data?.turns ?? []).map(turn => <div key={turn.turn_id} className={`card ${turn.role === 'user' ? '' : 'chat-assistant'}`} style={{ marginBottom: '.7rem' }}><div className="meta">{turn.role === 'user' ? 'You' : 'Atlas'}</div><div style={{ whiteSpace: 'pre-wrap' }}>{turn.content}</div></div>)}
        {pendingAction ? <ConfirmationCard item={pendingAction as never} onDone={async () => { await qc.invalidateQueries({ queryKey: ['conversation', selected] }); await qc.invalidateQueries({ queryKey: ['pending-actions'] }) }} /> : null}
      </div>
      <form className="composer" onSubmit={submit}><textarea value={message} onChange={e => setMessage(e.target.value)} placeholder="Ask Atlas…" /><button className="primary" type="submit" disabled={send.isPending || !message.trim()}>{send.isPending ? 'Working…' : 'Send'}</button></form>
    </div>
  </Workspace>
}
