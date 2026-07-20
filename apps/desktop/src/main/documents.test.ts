import { createHash } from 'node:crypto'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { createMainDocumentsClient } from './documents.js'

const artifactId = 'art_ABCDEFGHIJKLMNOPQRSTUVWX'
const pdf = new TextEncoder().encode('%PDF-1.7\ntrusted fixture\n%%EOF\n')
const hash = createHash('sha256').update(pdf).digest('hex')
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
        artifacts: [{
          artifact_id: artifactId,
          job_id: 'job-7',
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
          preview_available: true
        }]
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }) as typeof fetch

    const state = await client().list('job-7')

    expect(state.currentArtifactId).toBe(artifactId)
    expect(state.artifacts[0]?.previewAvailable).toBe(true)
  })

  it('accepts only matching registered PDF bytes and headers', async () => {
    globalThis.fetch = vi.fn(async () => artifactResponse()) as typeof fetch

    const result = await client().loadPdf(artifactId)

    expect(result.artifactRevision).toBe('render-2')
    expect(result.sha256).toBe(hash)
    expect(Array.from(new Uint8Array(result.bytes))).toEqual(Array.from(pdf))
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
})
