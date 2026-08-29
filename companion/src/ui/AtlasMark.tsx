/** The Atlas brand glyph, straight from UI groundwork/'s reference SVG. */
export function AtlasMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 200 200" aria-hidden xmlns="http://www.w3.org/2000/svg">
      <circle cx="100" cy="100" r="78" fill="none" stroke="#c9a24a" strokeWidth="3" />
      <ellipse cx="100" cy="100" rx="78" ry="30" fill="none" stroke="#c9a24a" strokeWidth="1.6" opacity="0.6" />
      <ellipse cx="100" cy="100" rx="30" ry="78" fill="none" stroke="#c9a24a" strokeWidth="1.6" opacity="0.6" />
      <circle cx="100" cy="100" r="4" fill="#e8c874" />
    </svg>
  )
}
