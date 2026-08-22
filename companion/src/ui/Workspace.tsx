import { useState, type ReactNode } from 'react'

type WorkspaceProps = {
  title: string
  subtitle?: ReactNode
  crumb?: ReactNode
  headerActions?: ReactNode
  rail?: ReactNode
  railLabel?: string
  context?: ReactNode
  contextLabel?: string
  children: ReactNode
  /** Fill the main viewport height (Chat). */
  fillHeight?: boolean
  className?: string
  banner?: ReactNode
}

/**
 * Shared Companion workspace anatomy:
 * header → optional secondary rail → primary → optional context rail.
 */
export function Workspace({
  title,
  subtitle,
  crumb,
  headerActions,
  rail,
  railLabel = 'Browse',
  context,
  contextLabel = 'Details',
  children,
  fillHeight = false,
  className = '',
  banner,
}: WorkspaceProps) {
  const [railOpen, setRailOpen] = useState(false)
  const [contextOpen, setContextOpen] = useState(false)
  const hasRail = Boolean(rail)
  const hasContext = Boolean(context)

  return (
    <div
      className={[
        'workspace',
        fillHeight ? 'workspace-fill' : '',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <header className="workspace-header">
        <div className="workspace-header-copy">
          {crumb ? <div className="crumb">{crumb}</div> : null}
          <h1>{title}</h1>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {headerActions ? (
          <div className="workspace-header-actions actions">{headerActions}</div>
        ) : null}
      </header>

      {banner}

      {(hasRail || hasContext) && (
        <div className="workspace-mobile-toggles actions">
          {hasRail ? (
            <button type="button" onClick={() => setRailOpen(true)}>
              {railLabel}
            </button>
          ) : null}
          {hasContext ? (
            <button type="button" onClick={() => setContextOpen(true)}>
              {contextLabel}
            </button>
          ) : null}
        </div>
      )}

      <div
        className={[
          'workspace-body',
          hasRail ? 'has-rail' : '',
          hasContext ? 'has-context' : '',
        ]
          .filter(Boolean)
          .join(' ')}
      >
        {hasRail ? (
          <aside className="workspace-rail" aria-label={railLabel}>
            {rail}
          </aside>
        ) : null}

        <section className="workspace-primary" aria-label="Primary workspace">
          {children}
        </section>

        {hasContext ? (
          <aside className="workspace-context" aria-label={contextLabel}>
            {context}
          </aside>
        ) : null}
      </div>

      {hasRail && railOpen ? (
        <WorkspaceSheet
          title={railLabel}
          onClose={() => setRailOpen(false)}
        >
          {rail}
        </WorkspaceSheet>
      ) : null}

      {hasContext && contextOpen ? (
        <WorkspaceSheet
          title={contextLabel}
          onClose={() => setContextOpen(false)}
        >
          {context}
        </WorkspaceSheet>
      ) : null}
    </div>
  )
}

function WorkspaceSheet({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  return (
    <div className="workspace-sheet" role="dialog" aria-modal="true">
      <button
        type="button"
        className="workspace-sheet-backdrop"
        aria-label="Close"
        onClick={onClose}
      />
      <div className="workspace-sheet-panel">
        <div className="workspace-sheet-top">
          <strong>{title}</strong>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="workspace-sheet-body">{children}</div>
      </div>
    </div>
  )
}

export function WorkspaceRailSection({
  title,
  children,
  actions,
}: {
  title?: string
  children: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="workspace-rail-section">
      {title || actions ? (
        <div className="workspace-rail-section-head">
          {title ? <h2>{title}</h2> : <span />}
          {actions ? <div className="actions">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </div>
  )
}

export function LifecycleControls({ children }: { children: ReactNode }) {
  return <div className="lifecycle-controls actions">{children}</div>
}
