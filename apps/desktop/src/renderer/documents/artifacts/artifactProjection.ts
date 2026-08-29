import type { ArtifactMediaType, DocumentArtifact, DocumentKey } from '../../../shared/contracts'

export const PDF: ArtifactMediaType = 'application/pdf'
export const DOCX: ArtifactMediaType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

export const documentOrder: DocumentKey[] = ['resume', 'cover_letter', 'references']

export const documentLabels: Record<DocumentKey, string> = {
  resume: 'Resume',
  cover_letter: 'Cover Letter',
  references: 'References'
}

export interface LogicalRevision {
  documentKey: DocumentKey
  documentLabel: string
  sourceRevision: string
  renderSequence: number
  artifacts: DocumentArtifact[]
  representative: DocumentArtifact
}

export interface LogicalDocument {
  documentKey: DocumentKey
  documentLabel: string
  revisions: LogicalRevision[]
}

export interface ArtifactSelection {
  document: LogicalDocument
  revision: LogicalRevision
}

export interface ArtifactProjection {
  documents: LogicalDocument[]
  selectedPreview: ArtifactSelection | null
}

export function latestArtifactByFormat(artifacts: DocumentArtifact[], mediaType: ArtifactMediaType) {
  return artifacts
    .filter(artifact => artifact.mediaType === mediaType)
    .sort((left, right) => right.renderSequence - left.renderSequence)[0]
}

function revisionRepresentative(artifacts: DocumentArtifact[]) {
  const succeeded = artifacts.filter(artifact => artifact.renderStatus === 'succeeded')
  return latestArtifactByFormat(succeeded, PDF)
    ?? latestArtifactByFormat(succeeded, DOCX)
    ?? latestArtifactByFormat(artifacts, PDF)
    ?? latestArtifactByFormat(artifacts, DOCX)
}

export function projectArtifactDocuments(artifacts: DocumentArtifact[]): LogicalDocument[] {
  return documentOrder.flatMap(documentKey => {
    const matching = artifacts.filter(artifact => artifact.documentKey === documentKey)
    if (!matching.length) return []
    const grouped = new Map<string, DocumentArtifact[]>()
    for (const artifact of matching) {
      const revision = grouped.get(artifact.sourceRevision) ?? []
      revision.push(artifact)
      grouped.set(artifact.sourceRevision, revision)
    }
    const revisions = Array.from(grouped.entries()).map(([sourceRevision, variants]) => ({
      documentKey,
      documentLabel: variants[0]?.documentLabel ?? documentLabels[documentKey],
      sourceRevision,
      renderSequence: Math.max(...variants.map(artifact => artifact.renderSequence)),
      artifacts: variants,
      representative: revisionRepresentative(variants)!
    })).sort((left, right) => right.renderSequence - left.renderSequence)
    return [{
      documentKey,
      documentLabel: revisions[0]?.documentLabel ?? documentLabels[documentKey],
      revisions
    }]
  })
}

export function selectArtifactRevision(documents: LogicalDocument[], artifactId: string | null): ArtifactSelection | null {
  if (!artifactId) return null
  for (const document of documents) {
    const revision = document.revisions.find(item => item.artifacts.some(artifact => artifact.artifactId === artifactId))
    if (revision) return { document, revision }
  }
  return null
}

export function projectArtifacts(artifacts: DocumentArtifact[], selectedArtifactId: string | null): ArtifactProjection {
  const documents = projectArtifactDocuments(artifacts)
  return { documents, selectedPreview: selectArtifactRevision(documents, selectedArtifactId) }
}

export function chooseArtifactPreview(artifacts: DocumentArtifact[], preferredArtifactId: string | null): ArtifactProjection {
  const projection = projectArtifacts(artifacts, preferredArtifactId)
  if (projection.selectedPreview?.revision.representative.renderStatus === 'succeeded') return projection
  for (const document of projection.documents) {
    const revision = document.revisions.find(item => item.representative.renderStatus === 'succeeded')
    if (revision) return { ...projection, selectedPreview: { document, revision } }
  }
  const document = projection.documents[0]
  const revision = document?.revisions[0]
  return {
    ...projection,
    selectedPreview: document && revision ? { document, revision } : null
  }
}
