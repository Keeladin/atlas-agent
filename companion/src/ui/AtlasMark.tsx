/** Atlas principal glyph. Geometry stays stable; colour follows the current surface spectrum. */
export function AtlasMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 200 200" aria-hidden xmlns="http://www.w3.org/2000/svg">
      <circle cx="100" cy="100" r="78" fill="none" stroke="#42d7ff" strokeWidth="3" />
      <ellipse cx="100" cy="100" rx="78" ry="30" fill="none" stroke="#42d7ff" strokeWidth="1.6" opacity="0.6" />
      <ellipse cx="100" cy="100" rx="30" ry="78" fill="none" stroke="#42d7ff" strokeWidth="1.6" opacity="0.6" />
      <circle cx="100" cy="100" r="4" fill="#9a7dff" />
    </svg>
  )
}
