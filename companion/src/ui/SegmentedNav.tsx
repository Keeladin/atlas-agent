import { NavLink } from 'react-router-dom'

/**
 * Lightweight in-destination segment switch — e.g. Work / Cadence. Purely
 * navigational: Cadence stays its own runtime concept (standing duties
 * that instantiate Work), this just keeps it reachable from the same
 * primary destination instead of a separate top-level nav slot.
 */
export function SegmentedNav({ items }: { items: Array<{ to: string; label: string; end?: boolean }> }) {
  return (
    <nav className="segmented-nav" aria-label="Section">
      {items.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end}>
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}
