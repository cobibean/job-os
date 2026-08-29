import { describe, expect, it } from 'vitest'

import type { DocxBinding } from '../../../../shared/docxDocuments'
import { displayDocxFilename } from './docxDisplay'

function binding(overrides: Partial<DocxBinding> = {}): DocxBinding {
  return {
    schemaVersion: 1,
    bindingId: 'docx_abc123',
    jobId: '(FAKE)-job',
    documentKey: 'resume',
    documentLabel: 'Resume',
    canonicalPath: '/tmp/resume.docx',
    filename: 'resume.docx',
    sha256: 'a'.repeat(64),
    byteLength: 4,
    modifiedAtMs: 1,
    revision: 1,
    capabilities: { mode: 'editable', protectedBlockCount: 0, editableBlockCount: 1, reasons: [] },
    createdAt: '2026-08-08T00:00:00.000Z',
    updatedAt: '2026-08-08T00:00:00.000Z',
    ...overrides
  }
}

describe('displayDocxFilename', () => {
  it('hides an app-owned binding prefix while preserving ordinary filenames', () => {
    expect(displayDocxFilename(binding({
      canonicalPath: '/Users/example/Library/Application Support/@jobos/desktop/editable-docx-artifacts/docx_abc123-resume.docx',
      filename: 'docx_abc123-resume.docx'
    }))).toBe('resume.docx')

    expect(displayDocxFilename(binding({ filename: 'docx_abc123-resume.docx' }))).toBe('docx_abc123-resume.docx')
  })

  it('also hides the collision suffix from an app-owned filename', () => {
    expect(displayDocxFilename(binding({
      canonicalPath: '/Users/example/Library/Application Support/@jobos/desktop/editable-docx-artifacts/docx_abc123-0123456789ab-resume.docx',
      filename: 'docx_abc123-0123456789ab-resume.docx'
    }))).toBe('resume.docx')
  })
})
