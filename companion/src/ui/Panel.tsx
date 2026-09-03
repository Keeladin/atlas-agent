import type { ReactNode } from 'react'

export function Panel({
  title,
  children,
  tone,
  className = '',
}: {
  title?: string
  children: ReactNode
  tone?: 'attention' | 'failed'
  className?: string
}) {
  const toneClass = tone ? ` ${tone}` : ''
  return (
    <section className={`card${toneClass} ${className}`.trim()}>
      {title ? <h2>{title}</h2> : null}
      {children}
    </section>
  )
}
