import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
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

const AUTHORITIES = [
  'read',
  'interpret',
  'recommend',
  'modify_internal',
  'communicate',
  'execute_external',
]

export function WorkNew() {
  const navigate = useNavigate()
  const [objective, setObjective] = useState('')
  const [notes, setNotes] = useState('')
  const [brief, setBrief] = useState<TaskBrief | null>(null)
  const [unsupported, setUnsupported] = useState<UnsupportedBrief | null>(null)
  const [unavailable, setUnavailable] = useState<UnavailableAcceptance | null>(null)
  const [authority, setAuthority] = useState('read')
  const [to, setTo] = useState('')
  const [subject, setSubject] = useState('')
  const [error, setError] = useState<string | null>(null)

  const briefMutation = useMutation({
    mutationFn: () =>
      api<BriefResult>('/api/advanced/brief', {
        method: 'POST',
        body: JSON.stringify({ objective, notes: notes || null }),
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
      setAuthority(data.required_authority)
    },
    onError: (err: Error) => {
      setBrief(null)
      setUnsupported(null)
      setUnavailable(null)
      setError(err.message)
    },
  })

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
          authority_scope: authority,
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

  function onPlan(event: FormEvent) {
    event.preventDefault()
    briefMutation.mutate()
  }

  const rail = (
    <Panel>
      <WorkspaceRailSection title="Intent">
        <form onSubmit={onPlan}>
          <div className="field">
            <label htmlFor="objective">What should Atlas do?</label>
            <textarea
              id="objective"
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="notes">Notes / context</label>
            <textarea
              id="notes"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          </div>
          <div className="workspace-rail-actions">
            <button
              className="primary"
              type="submit"
              disabled={briefMutation.isPending}
            >
              {briefMutation.isPending ? 'Planning…' : 'Create plan'}
            </button>
          </div>
        </form>
      </WorkspaceRailSection>
      <WorkspaceRailSection title="Sources">
        <p className="meta" style={{ margin: 0 }}>
          Planning uses your objective and notes. Attachments and knowledge
          sources will land here later.
        </p>
      </WorkspaceRailSection>
    </Panel>
  )

  const context = (
    <div className="stack">
      <Panel title="Permissions">
        {brief ? (
          <>
            <div className="field">
              <label htmlFor="authority">Authority for this work</label>
              <select
                id="authority"
                value={authority}
                onChange={(event) => setAuthority(event.target.value)}
              >
                {AUTHORITIES.map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </select>
            </div>
            <p className="meta">
              Proposed permission: {brief.required_authority}. You can raise or
              keep it before accepting.
            </p>
          </>
        ) : (
          <p className="empty" style={{ margin: 0 }}>
            Create a plan to review permissions.
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
        {brief && !unavailable ? (
          <div className="actions" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
            <button
              className="primary"
              type="button"
              disabled={acceptMutation.isPending}
              onClick={() => acceptMutation.mutate()}
            >
              I'll take this on
            </button>
            <button
              type="button"
              onClick={() => briefMutation.mutate()}
              disabled={briefMutation.isPending}
            >
              Revise plan
            </button>
          </div>
        ) : unsupported || unavailable ? (
          <button
            type="button"
            onClick={() => briefMutation.mutate()}
            disabled={briefMutation.isPending}
          >
            Try again
          </button>
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
      subtitle="Describe what Atlas should own. Review the plan, grant the right permission, then accept it into durable work."
      railLabel="Intent"
      contextLabel="Review"
      rail={rail}
      context={context}
      banner={error ? <p className="error-text">{error}</p> : null}
    >
      <Panel title="Proposed plan">
        {unavailable ? (
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
            Create a plan from the intent rail. The readable proposal appears
            here.
          </p>
        ) : (
          <>
            <div className="brief-row">
              <span>Objective</span>
              <div>{brief.objective}</div>
            </div>
            <div className="brief-row">
              <span>Will do</span>
              <div>
                {brief.capabilities
                  .map((id) => humanCapabilityLabel(id))
                  .join(' · ')}
              </div>
            </div>
            <div className="brief-row">
              <span>Expected result</span>
              <div>{brief.expected_effect}</div>
            </div>
            {brief.notes ? (
              <div className="brief-row">
                <span>Notes</span>
                <div>{brief.notes}</div>
              </div>
            ) : null}
          </>
        )}
      </Panel>
    </Workspace>
  )
}
