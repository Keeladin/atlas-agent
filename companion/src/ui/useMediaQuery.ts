import { useEffect, useState } from 'react'

function matchesQuery(query: string) {
  if (typeof window === 'undefined') return false
  if (typeof window.matchMedia === 'function') return window.matchMedia(query).matches
  const maxWidth = query.match(/max-width:\s*(\d+)px/i)
  return maxWidth ? window.innerWidth <= Number(maxWidth[1]) : false
}

export function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => matchesQuery(query))

  useEffect(() => {
    const update = () => setMatches(matchesQuery(query))
    if (typeof window.matchMedia === 'function') {
      const media = window.matchMedia(query)
      media.addEventListener?.('change', update)
      return () => media.removeEventListener?.('change', update)
    }
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [query])

  return matches
}
