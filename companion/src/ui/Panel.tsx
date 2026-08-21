import type { ReactNode } from 'react'

export function Panel({
  title,
  children,
  tone,
}: {
  title?: string
  children: ReactNode
  tone?: 'default' | 'authority' | 'confirmation' | 'danger'
}) {
  const border =
    tone === 'authority'
      ? 'var(--authority)'
      : tone === 'confirmation'
        ? 'var(--confirmation)'
        : tone === 'danger'
          ? 'var(--danger)'
          : 'var(--border)'
  return (
    <section
      className="panel"
      style={{
        background: 'var(--bg-panel)',
        border: `1px solid ${border}`,
        borderRadius: 'var(--radius)',
        padding: '1rem',
        boxShadow: 'var(--shadow)',
      }}
    >
      {title ? (
        <h2 style={{ margin: '0 0 0.75rem', fontSize: '1rem' }}>{title}</h2>
      ) : null}
      {children}
    </section>
  )
}
