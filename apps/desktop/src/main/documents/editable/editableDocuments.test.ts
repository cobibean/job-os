// @vitest-environment node

import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'

import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('./export/pdfExporter.js', () => ({
  exportEditableDocumentPdf: vi.fn(async () => new Uint8Array(Buffer.from('%PDF-1.4\njobos-test')))
}))

import { exportEditableDocumentPdf } from './export/pdfExporter.js'
import { createBlankDocument, defaultDocumentSettings } from '../../../shared/editableDocumentSchema.js'
import {
  createMainEditableDocumentsClient,
  safeDocumentKey,
  safeEditableArtifactId,
  safeEditableDocumentId,
  safeEditableJobId,
  safeEditableSnapshotId
} from './editableDocuments.js'

const originalFetch = globalThis.fetch
const documentId = 'edoc_ABCDEFGHIJKLMNOPQRSTUVWX'
const snapshotId = 'dsnap_ABCDEFGHIJKLMNOPQRSTUVWX'

function apiDocument(documentKey: 'resume' | 'cover_letter' | 'references' = 'references') {
  const settings = defaultDocumentSettings()
  const labels = { resume: 'Resume', cover_letter: 'Cover Letter', references: 'References' } as const
  return {
    schema_version: 1,
    document_id: documentId,
    job_id: 'job-7',
    document_key: documentKey,
    document_label: labels[documentKey],
    revision: 1,
    content: createBlankDocument(documentKey),
    settings: {
      page_size: settings.pageSize,
      orientation: settings.orientation,
      margins_inches: settings.marginsInches,
      default_font_family: settings.defaultFontFamily,
      default_font_size_pt: settings.defaultFontSizePt,
      header: {
        left: settings.header.left,
        center: settings.header.center,
        right: settings.header.right,
        first_page_different: settings.header.firstPageDifferent
      },
      footer: {
        left: settings.footer.left,
        center: settings.footer.center,
        right: settings.footer.right,
        first_page_different: settings.footer.firstPageDifferent
      },
      show_page_numbers: settings.showPageNumbers
    },
    comments: [],
    source_artifact_id: null,
    source_filename: null,
    source_sha256: null,
    published_revision: null,
    import_report: { source_filename: null, imported_at: null, issues: [] },
    created_at: '2026-08-07T00:00:00Z',
    updated_at: '2026-08-07T00:00:00Z'
  }
}

afterEach(() => {
  globalThis.fetch = originalFetch
  vi.restoreAllMocks()
})

describe('editable document main client', () => {
  it('rejects path-like and malformed opaque identifiers before any request', () => {
    expect(() => safeEditableJobId('../job')).toThrow('Invalid job')
    expect(() => safeEditableJobId(`job${String.fromCharCode(0)}id`)).toThrow('Invalid job')
    expect(() => safeEditableDocumentId('edoc_short')).toThrow('Invalid editable document')
    expect(() => safeEditableSnapshotId('dsnap_short')).toThrow('Invalid document snapshot')
    expect(() => safeEditableArtifactId('../../artifact')).toThrow('Invalid artifact')
    expect(() => safeDocumentKey('portfolio')).toThrow('Invalid document type')
  })

  it('creates a blank document through the authenticated typed API route', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(
      JSON.stringify(apiDocument()),
      { status: 201, headers: { 'Content-Type': 'application/json' } }
    ))
    globalThis.fetch = fetchMock as typeof fetch
    const client = createMainEditableDocumentsClient({
      baseUrl: 'http://127.0.0.1:8766',
      deviceToken: 'test-device-token'
    })

    const created = await client.createBlank('job-7', 'references', 'create-references-1')

    expect(created.documentId).toBe(documentId)
    expect(created.documentKey).toBe('references')
    expect(created.documentLabel).toBe('References')
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      'http://127.0.0.1:8766/v1/jobs/job-7/editable-documents'
    )
    const init = fetchMock.mock.calls[0]?.[1]
    expect(init?.method).toBe('POST')
    expect(init?.headers).toEqual(expect.objectContaining({
      Authorization: 'Bearer test-device-token',
      'Content-Type': 'application/json'
    }))
    expect(JSON.parse(String(init?.body))).toEqual({
      mode: 'blank',
      document_key: 'references',
      idempotency_key: 'create-references-1'
    })
  })

  it('rejects malformed canonical content locally before saving', async () => {
    const fetchMock = vi.fn()
    globalThis.fetch = fetchMock as typeof fetch
    const client = createMainEditableDocumentsClient({
      baseUrl: 'http://127.0.0.1:8766',
      deviceToken: 'test-device-token'
    })

    await expect(client.save(documentId, {
      baseRevision: 1,
      content: { type: 'doc', content: [{ type: 'script' }] },
      settings: defaultDocumentSettings(),
      comments: [],
      idempotencyKey: 'save-1'
    })).rejects.toThrow('Unknown node')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('imports a checksum-verified registered DOCX through local normalization', async () => {
    const artifactId = 'art_ABCDEFGHIJKLMNOP'
    const bytes = new Uint8Array(await readFile(
      new URL('./import/fixtures/one-page-resume.docx', import.meta.url)
    ))
    const digest = createHash('sha256').update(bytes).digest('hex')
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const route = String(input)
      if (route.endsWith(`/v1/artifacts/${artifactId}/download`)) {
        return new Response(bytes, {
          status: 200,
          headers: {
            'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'Content-Disposition': "attachment; filename*=UTF-8''resume.docx",
            'X-Artifact-ID': artifactId,
            'X-Artifact-Revision': 'source-1',
            'X-Source-Revision': 'source-1',
            'X-Content-SHA256': digest
          }
        })
      }
      if (route.endsWith('/v1/jobs/job-7/editable-documents') && fetchMock.mock.calls.length === 2) {
        return new Response(JSON.stringify({ documents: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        })
      }
      return new Response(JSON.stringify(apiDocument('resume')), {
        status: 201,
        headers: { 'Content-Type': 'application/json' }
      })
    })
    globalThis.fetch = fetchMock as typeof fetch
    const client = createMainEditableDocumentsClient({
      baseUrl: 'http://127.0.0.1:8766',
      deviceToken: 'test-device-token'
    })

    const imported = await client.importRegisteredArtifact('job-7', 'resume', artifactId)

    expect(imported.documentKey).toBe('resume')
    const createRequest = fetchMock.mock.calls[2]?.[1]
    const body = JSON.parse(String(createRequest?.body))
    expect(body).toEqual(expect.objectContaining({
      mode: 'import_registered_artifact',
      document_key: 'resume',
      source_artifact_id: artifactId
    }))
    expect(body.content.type).toBe('doc')
    expect(body.content.content[0].type).toBe('jobosSection')
  })

  it('returns null when the native DOCX picker is cancelled', async () => {
    const client = createMainEditableDocumentsClient({
      baseUrl: 'http://127.0.0.1:8766',
      deviceToken: 'token'
    }, { dialog: {
        showOpenDialog: vi.fn(async () => ({ canceled: true, filePaths: [] })),
        showSaveDialog: vi.fn(async () => ({ canceled: true, filePath: '/unused' }))
    } })

    await expect(client.importExternalDocx('job-7', 'resume')).resolves.toBeNull()
  })

  it('rejects a non-DOCX native selection before reading or uploading it', async () => {
    const fetchMock = vi.fn() as unknown as typeof fetch
    globalThis.fetch = fetchMock
    const client = createMainEditableDocumentsClient({
      baseUrl: 'http://127.0.0.1:8766',
      deviceToken: 'token'
    }, { dialog: {
        showOpenDialog: vi.fn(async () => ({ canceled: false, filePaths: ['/Users/example/resume.pdf'] })),
        showSaveDialog: vi.fn(async () => ({ canceled: true, filePath: '/unused' }))
    } })

    await expect(client.importExternalDocx('job-7', 'resume')).rejects.toThrow('Selected file must be a DOCX')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('returns authoritative PDF preview bytes and rejects export without native dialogs', async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(apiDocument('cover_letter')), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    })) as typeof fetch
    const client = createMainEditableDocumentsClient({
      baseUrl: 'http://127.0.0.1:8766',
      deviceToken: 'test-device-token'
    })

    await expect(client.importExternalDocx('job-7', 'cover_letter')).rejects.toThrow(
      'Native DOCX import is unavailable'
    )
    const preview = await client.preview(documentId)
    expect(Buffer.from(preview.bytes).subarray(0, 5).toString()).toBe('%PDF-')
    expect(preview.filename).toBe('Cover-Letter-r1.pdf')
    await expect(client.exportGenerated(documentId, 'docx')).rejects.toThrow(
      'Native document export is unavailable'
    )
  })

  it('publishes paired DOCX/PDF bytes from one saved canonical revision', async () => {
    vi.mocked(exportEditableDocumentPdf).mockClear()
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const response = {
        ...apiDocument('resume'),
        published_revision: init?.method === 'POST' ? 1 : null
      }
      return new Response(JSON.stringify(response), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    })
    globalThis.fetch = fetchMock as typeof fetch
    const client = createMainEditableDocumentsClient({
      baseUrl: 'http://127.0.0.1:8766',
      deviceToken: 'test-device-token'
    })

    await client.preview(documentId)
    const published = await client.publish(documentId)

    expect(published.publishedRevision).toBe(1)
    const request = fetchMock.mock.calls[2]?.[1]
    const body = JSON.parse(String(request?.body))
    expect(body).toEqual(expect.objectContaining({
      expected_revision: 1,
      docx_filename: 'Resume-r1.docx',
      pdf_filename: 'Resume-r1.pdf'
    }))
    expect(Buffer.from(body.docx_base64, 'base64').subarray(0, 2).toString()).toBe('PK')
    expect(Buffer.from(body.pdf_base64, 'base64').subarray(0, 5).toString()).toBe('%PDF-')
    expect(exportEditableDocumentPdf).toHaveBeenCalledTimes(1)
  })

  it('requires a clear confirmation and binds the unresolved count when publishing current state', async () => {
    const responseDocument = apiDocument('cover_letter')
    const paragraph = responseDocument.content.content![1]!.content![0]!
    paragraph.content = [{
      type: 'text',
      text: '(FAKE) JobHunter draft',
      marks: [{
        type: 'suggestion',
        attrs: {
          suggestionId: 'sug_jobhunter_publish',
          kind: 'insert',
          author: 'jobhunter',
          createdAt: '2026-08-21T00:00:00Z'
        }
      }]
    }]
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(
      JSON.stringify({ ...responseDocument, published_revision: init?.method === 'POST' ? 1 : null }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    ))
    globalThis.fetch = fetchMock as typeof fetch
    const showMessageBox = vi.fn(async () => ({ response: 0, checkboxChecked: false }))
    const client = createMainEditableDocumentsClient({
      baseUrl: 'http://127.0.0.1:8766',
      deviceToken: 'test-device-token'
    }, { dialog: {
      showMessageBox,
      showOpenDialog: vi.fn(async () => ({ canceled: true, filePaths: [] })),
      showSaveDialog: vi.fn(async () => ({ canceled: true, filePath: '' }))
    } })

    await client.publish(documentId)

    expect(showMessageBox).toHaveBeenCalledWith(expect.objectContaining({
      type: 'warning',
      defaultId: 1,
      cancelId: 1,
      message: 'The publish will use the exact current state shown in preview.'
    }))
    const body = JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))
    expect(body).toEqual(expect.objectContaining({
      expected_revision: 1,
      unresolved_suggestion_count: 1,
      confirm_current_state: true
    }))
  })

  it('accepts the exact snapshot identifier shape used by the API', () => {
    expect(safeEditableSnapshotId(snapshotId)).toBe(snapshotId)
  })
})
