import type { ReactNode } from 'react'

export function Inspect({
  label = 'Inspect details',
  children,
}: {
  label?: string
  children: ReactNode
}) {
  return (
    <details className="inspect">
      <summary>{label}</summary>
      {typeof children === 'string' ? <pre>{children}</pre> : children}
    </details>
  )
}
