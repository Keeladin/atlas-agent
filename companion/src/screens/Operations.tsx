import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { SourceRoot, WorkItem } from '../api/types'
import { Panel } from '../ui/Panel'
import { SegmentedNav } from '../ui/SegmentedNav'
import { Workspace } from '../ui/Workspace'
import { OPERATIONS_TABS } from './operationsNav'


type Artifact = {
  artifact_id: string
  display_name: string
  managed_content?: Record<string, unknown>
  managed_representations?: Array<Record<string, unknown>>
}

function stateClass(status: string) {
  if (status === 'completed') return 'done'
  if (status === 'active') return 'running'
  if (status === 'waiting_confirmation') return 'confirm'
  if (status === 'failed' || status === 'cancelled') return 'failed'
  return ''
}
export function Operations() {
  const roots = useQuery({ queryKey: ['source-roots'], queryFn: () => api<{ roots: SourceRoot[] }>('/api/sources/roots') })
  const work = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work') })
  const artifacts = useQuery({ queryKey: ['artifacts'], queryFn: () => api<{ artifacts: Artifact[] }>('/api/artifacts') })

  const rootRows = (roots.data?.roots ?? []).filter(root => root.enabled)
  const workRows = work.data?.work ?? []
  const artifactRows = artifacts.data?.artifacts ?? []
  const managed = artifactRows.filter(item => item.managed_content).length
  const linked = artifactRows.filter(item => item.managed_representations?.length).length
  const openWork = workRows.filter(item => !['completed', 'cancelled', 'failed'].includes(item.status)).length

  return <Workspace
    title="Operations"
    subtitle="Sources become governed artifacts; artifacts create responsibility; Work carries that responsibility to verified completion."
    tabs={<SegmentedNav items={OPERATIONS_TABS} />}
  >
    <div className="operations-flow" aria-label="Runtime flow">
      <Link to="/sources" className="flow-station"><span>01</span><strong>Sources</strong><small>{rootRows.length} enrolled</small></Link>
      <div className="flow-arrow" aria-hidden>→</div>
      <Link to="/sources" className="flow-station"><span>02</span><strong>Managed intake</strong><small>{managed} content objects</small></Link>
      <div className="flow-arrow" aria-hidden>→</div>
      <Link to="/work" className="flow-station"><span>03</span><strong>Work</strong><small>{openWork} open responsibilities</small></Link>
      <div className="flow-arrow" aria-hidden>→</div>
      <Link to="/sources" className="flow-station"><span>04</span><strong>Knowledge</strong><small>{linked} source links</small></Link>
    </div>

    <div className="operations-grid">
      <Panel title="Source intake">
        <div className="operations-list">
          {rootRows.map(root => <Link key={root.root_id} to="/sources" className="operations-row">
            <div><strong>{root.display_name}</strong><small className="mono">{root.root_id}</small></div>
            <span className="chip done">enrolled</span>
          </Link>)}
          {!rootRows.length && !roots.isLoading ? <p className="empty compact">No enrolled sources.</p> : null}
        </div>
        <div className="operations-summary"><span>Observed artifacts <strong>{artifactRows.length}</strong></span><span>Managed content <strong>{managed}</strong></span></div>
      </Panel>
      <Panel title="Responsibility">
        <div className="operations-list">
          {workRows.slice(0, 8).map(item => <Link key={item.work_id} to={`/work/${item.work_id}`} className="operations-row">
            <div><span className="eyebrow">{item.display_ref ?? 'Work'}</span><strong>{item.objective}</strong><small>{String(item.metadata?.workflow_intent ?? item.workflow_class ?? 'runtime work')}</small></div>
            <span className={`chip ${stateClass(item.status)}`}>{item.status.replaceAll('_', ' ')}</span>
          </Link>)}
          {!workRows.length && !work.isLoading ? <p className="empty compact">No durable Work yet.</p> : null}
        </div>
        <div className="operations-summary"><span>Open <strong>{openWork}</strong></span><span>Total <strong>{workRows.length}</strong></span></div>
      </Panel>
    </div>
  </Workspace>
}
