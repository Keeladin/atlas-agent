import { ArtifactObject } from './ArtifactObject'
import { CadenceObject } from './CadenceObject'
import { WorkObject } from './WorkObject'

export type RuntimeObjectDescriptor = { kind: 'work' | 'artifact' | 'cadence'; id: string }

export function RuntimeObject({ object }: { object: RuntimeObjectDescriptor }) {
  if (object.kind === 'work') return <WorkObject workId={object.id} />
  if (object.kind === 'artifact') return <ArtifactObject artifactId={object.id} />
  if (object.kind === 'cadence') return <CadenceObject cadenceId={object.id} />
  return null
}
