import { describe, expect, it } from 'vitest'

import type { DocumentArtifact } from '../../../shared/contracts'
import { chooseArtifactPreview, projectArtifacts } from './artifactProjection'

function artifact(overrides: Partial<DocumentArtifact> = {}): DocumentArtifact {
  return {
    artifactId: 'art_RESUMEPDFABCDEFGHIJKLMNOP',
    jobId: 'job-1',
    documentKey: 'resume',
    documentLabel: 'Resume',
    mediaType: 'application/pdf',
    artifactRevision: 'render-2',
    sourceRevision: 'source-2',
    renderSequence: 2,
    renderStatus: 'succeeded',
    filename: 'northstar-resume.pdf',
    sha256: 'a'.repeat(64),
    failureMessage: null,
    createdAt: '2026-08-29T12:00:00Z',
    previewAvailable: true,
    isCurrent: true,
    isLastSuccessful: true,
    isApproved: false,
    ...overrides
  }
}

describe('artifact projection', () => {
  it('groups formats into source revisions and orders documents and revisions deterministically', () => {
    const resumePdf = artifact()
    const resumeDocx = artifact({
      artifactId: 'art_RESUMEDOCXABCDEFGHIJKLMNO',
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      artifactRevision: 'render-3',
      renderSequence: 3,
      previewAvailable: false
    })
    const olderResume = artifact({
      artifactId: 'art_RESUMEOLDERABCDEFGHIJKLM',
      artifactRevision: 'render-1',
      sourceRevision: 'source-1',
      renderSequence: 1,
      isCurrent: false,
      isLastSuccessful: false
    })
    const cover = artifact({
      artifactId: 'art_COVERPDFABCDEFGHIJKLMNOPQ',
      documentKey: 'cover_letter',
      documentLabel: 'Cover Letter',
      artifactRevision: 'cover-1',
      sourceRevision: 'cover-source-1',
      renderSequence: 8
    })

    const projection = projectArtifacts([cover, olderResume, resumeDocx, resumePdf], resumeDocx.artifactId)

    expect(projection.documents.map(document => document.documentKey)).toEqual(['resume', 'cover_letter'])
    expect(projection.documents[0]?.revisions.map(revision => revision.sourceRevision)).toEqual(['source-2', 'source-1'])
    expect(projection.documents[0]?.revisions[0]?.artifacts).toEqual([resumeDocx, resumePdf])
    expect(projection.documents[0]?.revisions[0]?.representative).toBe(resumePdf)
    expect(projection.selectedPreview?.revision.sourceRevision).toBe('source-2')
  })

  it('keeps a successful preferred logical revision and otherwise uses the first successful fallback', () => {
    const older = artifact({
      artifactId: 'art_OLDERPDFABCDEFGHIJKLMNOPQ',
      artifactRevision: 'render-1',
      sourceRevision: 'source-1',
      renderSequence: 1,
      isCurrent: false,
      isLastSuccessful: true
    })
    const failed = artifact({
      artifactId: 'art_FAILEDPDFABCDEFGHIJKLMNOP',
      artifactRevision: 'render-3',
      sourceRevision: 'source-3',
      renderSequence: 3,
      renderStatus: 'failed',
      previewAvailable: false,
      sha256: null,
      failureMessage: 'Fixture render failed'
    })

    expect(chooseArtifactPreview([failed, older], older.artifactId).selectedPreview?.revision.sourceRevision).toBe('source-1')
    expect(chooseArtifactPreview([failed, older], failed.artifactId).selectedPreview?.revision.sourceRevision).toBe('source-1')
    expect(chooseArtifactPreview([failed], null).selectedPreview?.revision.sourceRevision).toBe('source-3')
    expect(chooseArtifactPreview([], null).selectedPreview).toBeNull()
  })
})
