import { createHash } from 'node:crypto'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { createMainDocumentsClient } from './documents.js'

const artifactId = 'art_ABCDEFGHIJKLMNOPQRSTUVWX'
const pdf = new TextEncoder().encode('%PDF-1.7\ntrusted fixture\n%%EOF\n')
const hash = createHash('sha256').update(pdf).digest('hex')
const docx = new Uint8Array([0x50, 0x4b, 0x03, 0x04])
const docxHash = createHash('sha256').update(docx).digest('hex')
const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
  vi.restoreAllMocks()
})

function artifactResponse() {
  return new Response(pdf, {
    status: 200,
    headers: {
      'Content-Type': 'application/pdf',
      'Content-Disposition': "inline; filename*=UTF-8''trusted-resume.pdf",
      'X-Artifact-ID': artifactId,
      'X-Artifact-Revision': 'render-2',
      'X-Source-Revision': 'source-2',
      'X-Content-SHA256': hash
    }
  })
}

function docxArtifactResponse() {
  return new Response(docx, {
    status: 200,
    headers: {
      'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'Content-Disposition': "attachment; filename*=UTF-8''trusted-original.docx",
      'X-Artifact-ID': artifactId,
      'X-Artifact-Revision': 'source-2',
      'X-Source-Revision': 'source-2',
      'X-Content-SHA256': docxHash
    }
  })
}

function client(cacheRoot = path.join(os.tmpdir(), 'jobos-documents-test-unused')) {
  return createMainDocumentsClient(
    { baseUrl: 'http://127.0.0.1:8766', deviceToken: 'test-device-token' },
    {
      cacheRoot,
      dialog: { showSaveDialog: vi.fn(async () => ({ canceled: true, filePath: '' })) },
      shell: { openPath: vi.fn(async () => ''), showItemInFolder: vi.fn() }
    }
  )
}

describe('trusted document client', () => {
  it('maps job artifacts and uses only the opaque job route', async () => {
    globalThis.fetch = vi.fn(async input => {
      expect(String(input)).toBe('http://127.0.0.1:8766/v1/jobs/job-7/artifacts')
      return new Response(JSON.stringify({
        job_id: 'job-7',
        current_artifact_id: artifactId,
        last_successful_artifact_id: artifactId,
        approved_artifact_id: artifactId,
        artifacts: [{
          artifact_id: artifactId,
          job_id: 'job-7',
          document_key: 'resume',
          document_label: 'Resume',
          render_sequence: 2,
          source_revision: 'source-2',
          artifact_revision: 'render-2',
          media_type: 'application/pdf',
          sha256: hash,
          render_status: 'succeeded',
          filename: 'trusted-resume.pdf',
          failure_message: null,
          created_at: '2026-07-20T00:00:00Z',
          is_current: true,
          is_last_successful: true,
          is_approved: true,
          preview_available: true
        }]
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }) as typeof fetch

    const state = await client().list('job-7')

    expect(state.currentArtifactId).toBe(artifactId)
    expect(state.approvedArtifactId).toBe(artifactId)
    expect(state.artifacts[0]?.isApproved).toBe(true)
    expect(state.artifacts[0]?.previewAvailable).toBe(true)
    expect(state.artifacts[0]?.documentKey).toBe('resume')
    expect(state.artifacts[0]?.renderSequence).toBe(2)
  })

  it('approves an exact artifact through its owning job route', async () => {
    globalThis.fetch = vi.fn(async input => {
      expect(String(input)).toBe(
        `http://127.0.0.1:8766/v1/jobs/job-7/artifacts/${artifactId}/approve`
      )
      return new Response(JSON.stringify({
        job_id: 'job-7', artifacts: [], current_artifact_id: artifactId,
        last_successful_artifact_id: artifactId, approved_artifact_id: artifactId
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }) as typeof fetch

    const result = await client().approve('job-7', artifactId)

    expect(result.approvedArtifactId).toBe(artifactId)
  })

  it('accepts only matching registered PDF bytes and headers', async () => {
    globalThis.fetch = vi.fn(async () => artifactResponse()) as typeof fetch

    const result = await client().loadPdf(artifactId)

    expect(result.artifactRevision).toBe('render-2')
    expect(result.sha256).toBe(hash)
    expect(Array.from(new Uint8Array(result.bytes))).toEqual(Array.from(pdf))
  })

  it('loads a checksum-verified DOCX original without exposing a file path', async () => {
    globalThis.fetch = vi.fn(async () => docxArtifactResponse()) as typeof fetch

    const result = await client().loadOriginalDocx(artifactId)

    expect(result.filename).toBe('trusted-original.docx')
    expect(result.sha256).toBe(docxHash)
    expect(Array.from(new Uint8Array(result.bytes))).toEqual(Array.from(docx))
    expect(result).not.toHaveProperty('path')
  })

  it('refuses a non-DOCX artifact on the Original boundary', async () => {
    globalThis.fetch = vi.fn(async () => artifactResponse()) as typeof fetch
    await expect(client().loadOriginalDocx(artifactId)).rejects.toThrow('Only DOCX')
  })

  it('rejects arbitrary IDs and mismatched content hashes before rendering', async () => {
    await expect(client().loadPdf('../../etc/passwd')).rejects.toThrow('Invalid artifact')
    globalThis.fetch = vi.fn(async () => {
      const response = artifactResponse()
      response.headers.set('X-Content-SHA256', '0'.repeat(64))
      return response
    }) as typeof fetch

    await expect(client().loadPdf(artifactId)).rejects.toThrow('SHA-256')
  })

  it('writes a verified cache file before native reveal', async () => {
    const cacheRoot = await mkdtemp(path.join(os.tmpdir(), 'jobos-documents-test-'))
    const showItemInFolder = vi.fn()
    globalThis.fetch = vi.fn(async () => artifactResponse()) as typeof fetch
    const documents = createMainDocumentsClient(
      { baseUrl: 'http://127.0.0.1:8766', deviceToken: 'test-device-token' },
      {
        cacheRoot,
        dialog: { showSaveDialog: vi.fn(async () => ({ canceled: true, filePath: '' })) },
        shell: { openPath: vi.fn(async () => ''), showItemInFolder }
      }
    )

    await documents.reveal(artifactId)
    const revealed = showItemInFolder.mock.calls[0]?.[0]

    expect(revealed).toContain(artifactId)
    expect(await readFile(revealed)).toEqual(Buffer.from(pdf))
    await rm(cacheRoot, { recursive: true, force: true })
  })

  it('exports the exact registered DOCX bytes with its filename', async () => {
    const outputRoot = await mkdtemp(path.join(os.tmpdir(), 'jobos-docx-export-test-'))
    const output = path.join(outputRoot, 'Example_User_Cover_Letter.docx')
    const docx = Buffer.from('PK\x03\x04trusted docx fixture')
    const docxHash = createHash('sha256').update(docx).digest('hex')
    globalThis.fetch = vi.fn(async () => new Response(docx, {
      status: 200,
      headers: {
        'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'Content-Length': String(docx.length),
        'Content-Disposition': "attachment; filename*=UTF-8''Example_User_Cover_Letter.docx",
        'X-Artifact-ID': artifactId,
        'X-Artifact-Revision': 'cover-docx-1',
        'X-Source-Revision': 'cover-source-1',
        'X-Content-SHA256': docxHash
      }
    })) as typeof fetch
    const documents = createMainDocumentsClient(
      { baseUrl: 'http://127.0.0.1:8766', deviceToken: 'test-device-token' },
      {
        cacheRoot: outputRoot,
        dialog: { showSaveDialog: vi.fn(async options => {
          expect(options.defaultPath).toBe('Example_User_Cover_Letter.docx')
          return { canceled: false, filePath: output, bookmark: '' }
        }) },
        shell: { openPath: vi.fn(async () => ''), showItemInFolder: vi.fn() }
      }
    )

    await expect(documents.exportArtifact(artifactId)).resolves.toBe(
      'Exported Example_User_Cover_Letter.docx'
    )
    expect(await readFile(output)).toEqual(docx)
    await rm(outputRoot, { recursive: true, force: true })
  })
})
