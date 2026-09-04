import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'

type Artifact = {
  artifact_id: string
  display_name: string
  media_type?: string | null
  created_at?: string | null
  facets?: Array<{ kind: string; state: string; root_id?: string | null; relative_path?: string | null }>
}

type Summary = Pick<Artifact, 'artifact_id' | 'display_name' | 'media_type' | 'created_at'>

function mediaKind(mediaType?: string | null) {
  const value = String(mediaType || '').toLowerCase()
  if (value.startsWith('image/')) return 'image'
  if (value === 'application/pdf') return 'pdf'
  if (value.startsWith('text/')) return 'text'
  return 'file'
}

export function ArtifactObject({ artifactId, summary, removable, onRemove }: {
  artifactId: string
  summary?: Summary
  removable?: boolean
  onRemove?: () => void
}) {
  const [preview, setPreview] = useState(false)
  const detail = useQuery({
    queryKey: ['artifact', artifactId],
    queryFn: () => api<{ artifact: Artifact }>(`/api/artifacts/${artifactId}`),
    enabled: !summary,
  })
  const artifact = summary ?? detail.data?.artifact
  const kind = mediaKind(artifact?.media_type)
  const contentUrl = `/api/artifacts/${artifactId}/content`
  return <section className={`runtime-object artifact-object ${preview ? 'expanded' : ''}`}>
    <div className="runtime-object-head">
      <span className="runtime-object-glyph" aria-hidden>{kind === 'image' ? '◫' : kind === 'pdf' ? '▤' : kind === 'text' ? '≡' : '◇'}</span>
      <span className="runtime-object-copy">
        <strong>{artifact?.display_name ?? 'Artifact'}</strong>
        <small>{artifact?.media_type || (detail.isLoading ? 'Reading artifact…' : 'Governed artifact')}</small>
      </span>
      <span className="runtime-object-actions">
        <button type="button" onClick={() => setPreview(value => !value)}>{preview ? 'Close' : 'Preview'}</button>
        {removable ? <button type="button" aria-label={`Remove ${artifact?.display_name ?? 'attachment'}`} onClick={onRemove}>×</button> : null}
      </span>
    </div>
    {preview ? <div className="artifact-preview">
      {kind === 'image' ? <img src={contentUrl} alt={artifact?.display_name ?? 'Artifact preview'} />
        : <iframe title={artifact?.display_name ?? 'Artifact preview'} src={contentUrl} />}
    </div> : null}
    {detail.isError ? <p className="runtime-object-error">{detail.error.message}</p> : null}
  </section>
}
