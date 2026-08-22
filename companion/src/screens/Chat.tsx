import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, MouseEvent, PointerEvent, UIEvent } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Conversation } from '../api/types'
import { nextActiveAfterDelete } from '../lib/conversationOwnership'
import { Inspect } from '../ui/Inspect'
import { Panel } from '../ui/Panel'
import { Workspace, WorkspaceRailSection } from '../ui/Workspace'

const NEAR_BOTTOM_PX = 72

function isHandoffUtterance(content: string) {
  return /^(please\s+)?((submit|send|start)\s+(this\s+)?(as\s+)?work|review(\s+it)?\s+in\s+work)(\s+please)?\s*[.!?]*$/i.test(
    content.trim(),
  )
}

export function Chat() {
  const queryClient = useQueryClient()
  const [activeId, setActiveId] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [stickToBottom, setStickToBottom] = useState(true)
  const [showJump, setShowJump] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(
    null,
  )
  const [renameId, setRenameId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const threadRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const bootstrapped = useRef(false)
  const pressTimer = useRef<number | null>(null)

  const listQuery = useQuery({
    queryKey: ['conversations', showArchived],
    queryFn: () =>
      api<{ conversations: Conversation[] }>(
        showArchived
          ? '/api/chat/conversations?archived=true'
          : '/api/chat/conversations',
      ),
  })

  const conversationQuery = useQuery({
    queryKey: ['conversation', activeId],
    queryFn: () => api<Conversation>(`/api/chat/conversations/${activeId}`),
    enabled: Boolean(activeId),
  })

  const scrollToLatest = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const el = threadRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior })
    setStickToBottom(true)
    setShowJump(false)
  }, [])

  const focusComposer = useCallback(() => {
    requestAnimationFrame(() => composerRef.current?.focus())
  }, [])

  const selectConversation = useCallback(
    (id: string, options?: { focus?: boolean }) => {
      setActiveId(id)
      setStickToBottom(true)
      setShowJump(false)
      if (options?.focus) focusComposer()
    },
    [focusComposer],
  )

  useEffect(() => {
    if (!stickToBottom) {
      setShowJump(true)
      return
    }
    scrollToLatest('auto')
  }, [
    activeId,
    conversationQuery.data?.turns?.length,
    stickToBottom,
    scrollToLatest,
  ])

  const createMutation = useMutation({
    mutationFn: () =>
      api<Conversation>('/api/chat/conversations', {
        method: 'POST',
        body: JSON.stringify({ title: 'Chat' }),
      }),
    onSuccess: (conversation) => {
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      selectConversation(conversation.id, { focus: true })
    },
  })

  const createConversation = createMutation.mutate
  const creating = createMutation.isPending

  const patchMutation = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string
      body: { title?: string; pinned?: boolean; archived?: boolean }
    }) =>
      api<Conversation>(`/api/chat/conversations/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      void queryClient.invalidateQueries({ queryKey: ['conversation'] })
      setRenameId(null)
      setMenu(null)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      api<{ deleted: string }>(`/api/chat/conversations/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: (_data, id) => {
      const nextId = nextActiveAfterDelete(
        id,
        listQuery.data?.conversations || [],
        activeId,
      )
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      setDeleteId(null)
      setMenu(null)
      if (activeId !== id) return
      if (nextId) {
        selectConversation(nextId, { focus: true })
        return
      }
      setShowArchived(false)
      setActiveId(null)
      bootstrapped.current = false
      createConversation()
    },
  })

  useEffect(() => {
    if (deleteId) setMenu(null)
  }, [deleteId])

  useEffect(() => {
    if (bootstrapped.current || activeId || !listQuery.isSuccess) return
    const list = listQuery.data?.conversations || []
    if (list.length > 0) {
      bootstrapped.current = true
      selectConversation(list[0].id)
      return
    }
    if (creating) return
    bootstrapped.current = true
    createConversation()
  }, [
    activeId,
    creating,
    createConversation,
    listQuery.isSuccess,
    listQuery.data?.conversations,
    selectConversation,
  ])

  function onThreadScroll(event: UIEvent<HTMLDivElement>) {
    const el = event.currentTarget
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    const nearBottom = distance <= NEAR_BOTTOM_PX
    setStickToBottom(nearBottom)
    setShowJump(!nearBottom)
  }

  const sendMutation = useMutation({
    mutationFn: () =>
      api(`/api/chat/conversations/${activeId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ message }),
      }),
    onSuccess: () => {
      setMessage('')
      setStickToBottom(true)
      void queryClient.invalidateQueries({ queryKey: ['conversation', activeId] })
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      focusComposer()
    },
  })

  function onSend(event: FormEvent) {
    event.preventDefault()
    if (!activeId || !message.trim()) return
    sendMutation.mutate()
  }

  const turns = conversationQuery.data?.turns || []
  const ready = Boolean(activeId)
  const lastUserTurn = [...turns]
    .reverse()
    .find((turn) => turn.role === 'user' && !isHandoffUtterance(turn.content))
  const workHandoffSearch = new URLSearchParams()
  if (activeId) workHandoffSearch.set('conversation', activeId)
  if (lastUserTurn?.id) workHandoffSearch.set('until', lastUserTurn.id)
  const workHandoffTo = activeId
    ? `/work/new?${workHandoffSearch.toString()}`
    : '/work/new'

  const rail = (
    <Panel className="chat-rail" title="Conversations">
      <div className="workspace-rail-actions">
        <button
          className="primary"
          type="button"
          onClick={() => createMutation.mutate()}
          disabled={createMutation.isPending}
        >
          New chat
        </button>
        <Link to={workHandoffTo}>
          <button className="primary" type="button" disabled={!activeId}>
            Send to Work
          </button>
        </Link>
      </div>
      <WorkspaceRailSection
        title={showArchived ? 'Archive' : 'Recents'}
        actions={
          <button
            className="ghost"
            type="button"
            onClick={() => setShowArchived((value) => !value)}
          >
            {showArchived ? 'Recents' : 'Archive'}
          </button>
        }
      >
        <div className="chat-rail-scroll">
          {(listQuery.data?.conversations || []).map((item) => (
            <RecentRow
              key={item.id}
              item={item}
              active={item.id === activeId}
              renaming={renameId === item.id}
              renameValue={renameValue}
              onRenameValue={setRenameValue}
              onCommitRename={() => {
                if (!renameValue.trim()) return
                patchMutation.mutate({
                  id: item.id,
                  body: { title: renameValue.trim() },
                })
              }}
              onCancelRename={() => setRenameId(null)}
              confirmingDelete={deleteId === item.id}
              onSelect={() => selectConversation(item.id)}
              onMenu={(event) => {
                event.preventDefault()
                event.stopPropagation()
                if (deleteId) return
                setMenu({ id: item.id, x: event.clientX, y: event.clientY })
              }}
              onLongPress={() =>
                setMenu({ id: item.id, x: 24, y: 120 })
              }
              pressTimer={pressTimer}
            />
          ))}
          {!listQuery.data?.conversations?.length && !createMutation.isPending ? (
            <p className="empty">
              {showArchived ? 'Nothing in the archive.' : 'No conversations yet.'}
            </p>
          ) : null}
        </div>
      </WorkspaceRailSection>
      {menu && !deleteId ? (
        <ConversationMenu
          item={(listQuery.data?.conversations || []).find((row) => row.id === menu.id)}
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          onRename={() => {
            const target = (listQuery.data?.conversations || []).find(
              (row) => row.id === menu.id,
            )
            setRenameId(menu.id)
            setRenameValue(target?.title || '')
            setMenu(null)
          }}
          onPin={() => {
            const target = (listQuery.data?.conversations || []).find(
              (row) => row.id === menu.id,
            )
            patchMutation.mutate({
              id: menu.id,
              body: { pinned: !target?.pinned },
            })
          }}
          onArchive={() => {
            const target = (listQuery.data?.conversations || []).find(
              (row) => row.id === menu.id,
            )
            patchMutation.mutate({
              id: menu.id,
              body: { archived: !target?.archived },
            })
          }}
          onDelete={() => {
            const id = menu.id
            setMenu(null)
            setDeleteId(id)
          }}
        />
      ) : null}
      {deleteId ? (
        <div
          className="menu-confirm"
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <p className="meta" style={{ marginTop: 0 }}>
            Delete this conversation and its messages?
          </p>
          <div className="actions">
            <button
              className="danger"
              type="button"
              aria-label="Delete conversation permanently"
              disabled={deleteMutation.isPending}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.preventDefault()
                event.stopPropagation()
                deleteMutation.mutate(deleteId)
              }}
            >
              Delete
            </button>
            <button
              type="button"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.preventDefault()
                event.stopPropagation()
                setDeleteId(null)
              }}
            >
              Keep
            </button>
          </div>
        </div>
      ) : null}
    </Panel>
  )

  const context = (
    <Panel className="chat-context-pane" title="Context">
      <div className="chat-context-scroll">
        <p className="meta" style={{ marginTop: 0 }}>
          Helpful next steps — not runtime noise.
        </p>
        <div className="list-row">
          <div>
            <strong>This needs Work</strong>
            <div className="meta">
              Atlas will prepare a plan. You still authorize execution.
            </div>
          </div>
        </div>
        <div className="list-row">
          <div>
            <strong>Review in Work</strong>
            <div className="meta">Send this conversation as a Work plan</div>
          </div>
          <Link to={workHandoffTo}>
            <button className="ghost" type="button" disabled={!activeId}>
              Review in Work
            </button>
          </Link>
        </div>
        <Inspect label="Inspect conversation details">
          {JSON.stringify(
            {
              conversation_id: activeId,
              turn_count: turns.length,
            },
            null,
            2,
          )}
        </Inspect>
      </div>
    </Panel>
  )

  return (
    <Workspace
      title="Chat"
      subtitle="Talk with Atlas. Send to Work when talk becomes a responsibility — that reviews a plan, it does not run it."
      fillHeight
      className="chat-page"
      railLabel="Conversations"
      contextLabel="Context"
      rail={rail}
      context={context}
      banner={
        listQuery.isError || conversationQuery.isError ? (
          <div className="offline-banner">
            Could not reach chat. Check your connection or sign in again.
          </div>
        ) : null
      }
    >
      <Panel className="chat-thread-pane">
        <div className="chat-thread-body">
          {!ready ? (
            <p className="empty">Opening chat…</p>
          ) : (
            <>
              <div className="chat-thread-title">
                <strong>
                  {conversationQuery.data?.title || 'Conversation'}
                </strong>
              </div>
              <div className="chat-thread-wrap">
                <div
                  className="thread"
                  ref={threadRef}
                  onScroll={onThreadScroll}
                >
                  {turns.map((turn) => (
                    <div
                      key={turn.id}
                      className={`bubble ${turn.role === 'user' ? 'user' : ''}`}
                    >
                      <div className="who">
                        {turn.role === 'user' ? 'You' : 'Atlas'}
                      </div>
                      {turn.content}
                    </div>
                  ))}
                  {!turns.length ? (
                    <p className="empty">Say hello to start.</p>
                  ) : null}
                  {sendMutation.isError ? (
                    <p className="error-text">
                      {(sendMutation.error as Error).message}
                    </p>
                  ) : null}
                </div>
                {showJump ? (
                  <button
                    type="button"
                    className="jump-latest"
                    onClick={() => scrollToLatest('smooth')}
                  >
                    Jump to latest
                  </button>
                ) : null}
              </div>
              <form className="composer" onSubmit={onSend}>
                <textarea
                  ref={composerRef}
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder="Message Atlas…"
                  required
                />
                <button
                  className="primary"
                  type="submit"
                  disabled={sendMutation.isPending}
                >
                  Send
                </button>
              </form>
            </>
          )}
        </div>
      </Panel>
    </Workspace>
  )
}

function RecentRow({
  item,
  active,
  renaming,
  renameValue,
  onRenameValue,
  onCommitRename,
  onCancelRename,
  confirmingDelete,
  onSelect,
  onMenu,
  onLongPress,
  pressTimer,
}: {
  item: Conversation
  active: boolean
  renaming: boolean
  renameValue: string
  onRenameValue: (value: string) => void
  onCommitRename: () => void
  onCancelRename: () => void
  confirmingDelete: boolean
  onSelect: () => void
  onMenu: (event: MouseEvent<HTMLElement>) => void
  onLongPress: () => void
  pressTimer: { current: number | null }
}) {
  function clearPress() {
    if (pressTimer.current != null) {
      window.clearTimeout(pressTimer.current)
      pressTimer.current = null
    }
  }

  function onPointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (event.pointerType !== 'touch') return
    clearPress()
    pressTimer.current = window.setTimeout(() => {
      onLongPress()
    }, 520)
  }

  if (renaming) {
    return (
      <form
        className="chat-recent-rename"
        onSubmit={(event) => {
          event.preventDefault()
          onCommitRename()
        }}
      >
        <input
          value={renameValue}
          onChange={(event) => onRenameValue(event.target.value)}
          aria-label="Conversation title"
          autoFocus
        />
        <div className="actions">
          <button className="primary" type="submit">
            Save
          </button>
          <button type="button" onClick={onCancelRename}>
            Cancel
          </button>
        </div>
      </form>
    )
  }

  return (
    <div className={`chat-recent-wrap${confirmingDelete ? ' confirming' : ''}`}>
      <button
        type="button"
        className={`ghost chat-recent-item${active ? ' active' : ''}`}
        onClick={onSelect}
        onContextMenu={onMenu}
        onPointerDown={onPointerDown}
        onPointerUp={clearPress}
        onPointerCancel={clearPress}
        onPointerLeave={clearPress}
      >
        <strong>
          {item.pinned ? 'Pinned · ' : ''}
          {item.title}
        </strong>
        <span className="meta">{item.turn_count} messages</span>
      </button>
      <button
        className="ghost chat-recent-more"
        type="button"
        aria-label="Conversation actions"
        onClick={onMenu}
      >
        ⋯
      </button>
    </div>
  )
}

function stopMenuEvent(event: { stopPropagation: () => void; preventDefault?: () => void }) {
  event.stopPropagation()
}

function ConversationMenu({
  item,
  x,
  y,
  onClose,
  onRename,
  onPin,
  onArchive,
  onDelete,
}: {
  item?: Conversation
  x: number
  y: number
  onClose: () => void
  onRename: () => void
  onPin: () => void
  onArchive: () => void
  onDelete: () => void
}) {
  if (!item) return null
  function runAction(
    event: MouseEvent<HTMLButtonElement>,
    action: () => void,
  ) {
    event.preventDefault()
    event.stopPropagation()
    action()
  }
  return createPortal(
    <div className="menu-root">
      <div
        className="menu-layer"
        role="presentation"
        onPointerDown={onClose}
      />
      <div
        className="menu"
        role="menu"
        style={{ top: y, left: x }}
        onPointerDown={stopMenuEvent}
        onClick={stopMenuEvent}
      >
        <button
          type="button"
          role="menuitem"
          onPointerDown={stopMenuEvent}
          onClick={(event) => runAction(event, onRename)}
        >
          Rename
        </button>
        <button
          type="button"
          role="menuitem"
          onPointerDown={stopMenuEvent}
          onClick={(event) => runAction(event, onPin)}
        >
          {item.pinned ? 'Unpin' : 'Pin'}
        </button>
        <button
          type="button"
          role="menuitem"
          onPointerDown={stopMenuEvent}
          onClick={(event) => runAction(event, onArchive)}
        >
          {item.archived ? 'Restore' : 'Archive'}
        </button>
        <button
          className="danger"
          type="button"
          role="menuitem"
          onPointerDown={stopMenuEvent}
          onClick={(event) => runAction(event, onDelete)}
        >
          Delete
        </button>
      </div>
    </div>,
    document.body,
  )
}
