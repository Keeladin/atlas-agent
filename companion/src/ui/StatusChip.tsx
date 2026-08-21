const COLORS: Record<string, string> = {
  planned: 'var(--text-muted)',
  active: 'var(--accent)',
  waiting: 'var(--warn)',
  waiting_confirmation: 'var(--confirmation)',
  waiting_authority: 'var(--authority)',
  running: 'var(--accent)',
  completed: 'var(--ok)',
  failed: 'var(--danger)',
  cancelled: 'var(--text-muted)',
  pending: 'var(--text-muted)',
  blocked: 'var(--warn)',
  pass: 'var(--ok)',
  confirmed: 'var(--ok)',
  denied: 'var(--danger)',
}

export function StatusChip({
  value,
  label,
}: {
  value: string
  label?: string
}) {
  const color = COLORS[value] || 'var(--text-muted)'
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.35rem',
        padding: '0.25rem 0.65rem',
        borderRadius: '999px',
        border: `1px solid ${color}`,
        color,
        fontSize: '0.8rem',
        fontWeight: 600,
        textTransform: 'lowercase',
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: color,
        }}
      />
      {label || value}
    </span>
  )
}
