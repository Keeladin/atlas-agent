import type { ReactNode } from 'react'

/** Shared bottom-sheet modal: mobile home for anything a side rail would show on desktop. */
export function Sheet({
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
