import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import {
  isUnavailableAcceptance,
  isUnsupportedBrief,
  type BriefResult,
  type TaskBrief,
  type UnavailableAcceptance,
  type UnsupportedBrief,
  type WorkDetail,
} from '../api/types'
import { humanCapabilityLabel } from '../lib/workLabels'
import { Inspect } from '../ui/Inspect'
import { Panel } from '../ui/Panel'
import { Workspace, WorkspaceRailSection } from '../ui/Workspace'

type BriefBody = {
  objective?: string
  notes?: string | null
  conversation_id?: string
  until_turn_id?: string
  revision?: string
}

export function WorkNew() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const conversationId = searchParams.get('conversation')?.trim() || ''
  const untilTurnId = searchParams.get('until')?.trim() || ''
  const fromChat = Boolean(conversationId)

  const [request, setRequest] = useState('')
  const [extra, setExtra] = useState('')
  const [revision, setRevision] = useState('')
  const [brief, setBrief] = useState<TaskBrief | null>(null)
  const [unsupported, setUnsupported] = useState<UnsupportedBrief | null>(null)
  const [unavailable, setUnavailable] = useState<UnavailableAcceptance | null>(null)
  const [to, setTo] = useState('')
  const [subject, setSubject] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [handoffStarted, setHandoffStarted] = useState(false)

  const briefMutation = useMutation({
    mutationFn: (body: BriefBody) =>
      api<BriefResult>('/api/advanced/brief', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      setError(null)
      if (isUnsupportedBrief(data)) {
        setBrief(null)
        setUnsupported(data)
        setUnavailable(null)
        return
      }
      if (!data.capabilities?.length) {
        setBrief(null)
        setUnsupported(null)
        setError('Atlas returned a plan without capabilities.')
        return
      }
      setUnsupported(null)
      setUnavailable(null)
      setBrief(data)
    },
    onError: (err: Error) => {
      setBrief(null)
      setUnsupported(null)
      setUnavailable(null)
      setError(err.message)
    },
  })

  useEffect(() => {
    if (!fromChat) return
    setHandoffStarted(true)
    const body: BriefBody = { conversation_id: conversationId }
    if (untilTurnId) body.until_turn_id = untilTurnId
    briefMutation.mutate(body)
    // Compile once per conversation pointer. briefMutation is unstable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, untilTurnId, fromChat])

  const acceptMutation = useMutation({
    mutationFn: async () => {
      if (!brief) throw new Error('Create a plan first')
      const inputs: Record<string, Record<string, string>> = {}
      if (brief.capabilities.includes('communication.email.send')) {
        inputs['communication.email.send'] = {
          ...(to ? { to } : {}),
          ...(subject ? { subject } : {}),
        }
      }
      return api<WorkDetail>('/api/work', {
        method: 'POST',
        body: JSON.stringify({
          brief,
          authority_scope: brief.required_authority,
          inputs: Object.keys(inputs).length ? inputs : undefined,
        }),
      })
    },
    onSuccess: (detail) => {
      if (isUnavailableAcceptance(detail)) {
        setUnavailable(detail)
        setError(null)
        return
      }
      navigate(`/work/${detail.work_id}`)
    },
    onError: (err: Error) => {
      if (err instanceof ApiError && isUnavailableAcceptance(err.body)) {
        setUnavailable(err.body)
        setError(null)
        return
      }
      setError(err.message)
    },
  })

  function compileDirect(event: FormEvent) {
    event.preventDefault()
    briefMutation.mutate({
      objective: request,
      notes: extra.trim() ? extra : null,
    })
  }

  function compileRevision(event: FormEvent) {
    event.preventDefault()
    const text = revision.trim()
    if (!text) return
    if (fromChat) {
      const body: BriefBody = {
        conversation_id: conversationId,
        revision: text,
      }
      if (untilTurnId) body.until_turn_id = untilTurnId
      briefMutation.mutate(body)
      return
    }
    briefMutation.mutate({
      objective: text,
      notes: extra.trim() ? extra : null,
    })
  }

  const planning = briefMutation.isPending
  const ready = Boolean(brief) && !unavailable
  const blocked = Boolean(unsupported || unavailable)

  const rail = (
    <Panel>
      {fromChat && !brief && !blocked ? (
        <WorkspaceRailSection title="From Chat">
          <p className="meta" style={{ margin: 0 }}>
            Atlas is planning from this conversation. You do not need to split
            the request into form fields.
          </p>
        </WorkspaceRailSection>
      ) : null}
      {!fromChat && !brief && !blocked ? (
        <WorkspaceRailSection title="Request">
          <form onSubmit={compileDirect}>
            <div className="field">
              <label htmlFor="request">Tell Atlas what you want done</label>
              <textarea
                id="request"
                value={request}
                onChange={(event) => setRequest(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="extra">Additional context (optional)</label>
              <textarea
                id="extra"
                value={extra}
                onChange={(event) => setExtra(event.target.value)}
              />
            </div>
            <div className="workspace-rail-actions">
              <button className="primary" type="submit" disabled={planning}>
                {planning ? 'Planning…' : 'Create plan'}
              </button>
            </div>
          </form>
        </WorkspaceRailSection>
      ) : null}
      {(brief || blocked) && !unavailable ? (
        <WorkspaceRailSection title="Revise">
          <form onSubmit={compileRevision}>
            <div className="field">
              <label htmlFor="revision">Tell Atlas how to change the plan</label>
              <textarea
                id="revision"
                value={revision}
                onChange={(event) => setRevision(event.target.value)}
                required
              />
            </div>
            <div className="workspace-rail-actions">
              <button className="primary" type="submit" disabled={planning}>
                {planning ? 'Planning…' : 'Revise plan'}
              </button>
            </div>
          </form>
        </WorkspaceRailSection>
      ) : null}
      {fromChat ? (
        <WorkspaceRailSection title="Source">
          <p className="meta" style={{ marginTop: 0 }}>
            This plan is from a Chat conversation. Accepting it does not change
            that conversation.
          </p>
          <Link to="/chat">Back to Chat</Link>
        </WorkspaceRailSection>
      ) : (
        <WorkspaceRailSection title="Sources">
          <p className="meta" style={{ margin: 0 }}>
            Planning uses your request. Attachments and knowledge sources will
            land here later.
          </p>
        </WorkspaceRailSection>
      )}
    </Panel>
  )

  const context = (
    <div className="stack">
      <Panel title="Permission needed">
        {brief ? (
          <>
            <p style={{ marginTop: 0 }}>{brief.required_authority}</p>
            <p className="meta">
              Atlas derived this from the plan. Reviewing or handing off from
              Chat does not grant it.
            </p>
          </>
        ) : (
          <p className="empty" style={{ margin: 0 }}>
            Appears after planning.
          </p>
        )}
      </Panel>

      <Panel title="Expected result">
        {brief ? (
          <p style={{ marginTop: 0 }}>{brief.expected_effect}</p>
        ) : unsupported || unavailable ? (
          <p style={{ marginTop: 0 }} className="meta">
            No executable plan yet.
          </p>
        ) : (
          <p className="empty" style={{ margin: 0 }}>
            Appears after planning.
          </p>
        )}
      </Panel>

      <Panel title="Constraints">
        {brief ? (
          <p style={{ marginTop: 0 }}>
            {brief.constraints.length
              ? brief.constraints.join(' · ')
              : 'None stated'}
          </p>
        ) : (
          <p className="empty" style={{ margin: 0 }}>
            Appears after planning.
          </p>
        )}
      </Panel>

      {brief?.capabilities.includes('communication.email.send') ? (
        <Panel title="Message details">
          <div className="field">
            <label htmlFor="to">Recipient</label>
            <input
              id="to"
              value={to}
              onChange={(event) => setTo(event.target.value)}
              placeholder="ops@example.invalid"
            />
          </div>
          <div className="field">
            <label htmlFor="subject">Subject</label>
            <input
              id="subject"
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
              placeholder="Weekly ops report"
            />
          </div>
        </Panel>
      ) : null}

      <Panel title="Accept">
        {ready ? (
          <div className="actions" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
            <button
              className="primary"
              type="button"
              disabled={acceptMutation.isPending}
              onClick={() => acceptMutation.mutate()}
            >
              Accept Work
            </button>
          </div>
        ) : blocked ? (
          <p className="empty" style={{ margin: 0 }}>
            Atlas cannot accept this as Work yet.
          </p>
        ) : (
          <p className="empty" style={{ margin: 0 }}>
            Accept becomes available after a plan is ready.
          </p>
        )}
        {brief ? (
          <Inspect label="Inspect technical plan">
            {JSON.stringify(brief, null, 2)}
          </Inspect>
        ) : null}
        {unsupported ? (
          <Inspect label="Inspect unsupported result">
            {JSON.stringify(unsupported, null, 2)}
          </Inspect>
        ) : null}
        {unavailable ? (
          <Inspect label="Inspect unavailable result">
            {JSON.stringify(unavailable, null, 2)}
          </Inspect>
        ) : null}
      </Panel>
    </div>
  )

  return (
    <Workspace
      title="Plan work"
      crumb={
        <>
          <Link to="/work">Work</Link> / Plan
        </>
      }
      subtitle={
        fromChat
          ? 'Atlas prepared this plan from Chat. Review it, then accept if you want Work to own it.'
          : 'Tell Atlas what you want done. Review the plan, then accept it into durable work.'
      }
      railLabel="Intent"
      contextLabel="Review"
      rail={rail}
      context={context}
      banner={error ? <p className="error-text">{error}</p> : null}
    >
      <Panel title="Proposed plan">
        {planning && !brief && !blocked ? (
          <p className="empty" style={{ margin: 0 }}>
            Atlas is preparing a plan…
          </p>
        ) : unavailable ? (
          <>
            <p className="empty" style={{ marginTop: 0 }}>
              I can't do this yet
            </p>
            <div className="brief-row">
              <span>Reason</span>
              <div>{unavailable.reason}</div>
            </div>
          </>
        ) : unsupported ? (
          <>
            <p className="empty" style={{ marginTop: 0 }}>
              I can't turn this into work yet
            </p>
            <div className="brief-row">
              <span>Reason</span>
              <div>{unsupported.reason}</div>
            </div>
            {unsupported.closest_capability ? (
              <div className="brief-row">
                <span>Closest match</span>
                <div>
                  {humanCapabilityLabel(unsupported.closest_capability)}
                </div>
              </div>
            ) : null}
          </>
        ) : !brief ? (
          <p className="empty" style={{ margin: 0 }}>
            {fromChat && handoffStarted
              ? 'Atlas is preparing a plan…'
              : 'Tell Atlas what you want done. The readable proposal appears here.'}
          </p>
        ) : (
          <>
            <div className="brief-row">
              <span>Work request</span>
              <div>{brief.objective}</div>
            </div>
            <div className="brief-row">
              <span>Atlas will</span>
              <div>
                <ul className="atlas-will">
                  {brief.capabilities.map((id) => (
                    <li key={id}>{humanCapabilityLabel(id)}</li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="brief-row">
              <span>Constraints</span>
              <div>
                {brief.constraints.length
                  ? brief.constraints.join(' · ')
                  : 'None stated'}
              </div>
            </div>
            <div className="brief-row">
              <span>Expected result</span>
              <div>{brief.expected_effect}</div>
            </div>
            <div className="brief-row">
              <span>Permission needed</span>
              <div>{brief.required_authority}</div>
            </div>
          </>
        )}
      </Panel>
    </Workspace>
  )
}
