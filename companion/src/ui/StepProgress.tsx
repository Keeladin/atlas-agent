import type { WorkDetail } from '../api/types'

/** Segment progress from step statuses only — no invented percentages. */
export function StepProgress({ steps }: { steps: WorkDetail['steps'] }) {
  if (!steps.length) return null
  const done = steps.filter((s) => s.status === 'pass' || s.status === 'skipped').length
  return (
    <div className="step-progress">
      <div className="step-progress-track" aria-hidden>
        {steps.map((step) => {
          let cls = ''
          if (step.status === 'pass' || step.status === 'skipped') cls = 'done'
          else if (step.status === 'running') cls = 'active'
          else if (step.status === 'blocked') cls = 'blocked'
          else if (step.status === 'failed') cls = 'failed'
          return <span key={step.id} className={cls} />
        })}
      </div>
      <div className="meta">
        {done} of {steps.length} steps complete
      </div>
    </div>
  )
}
