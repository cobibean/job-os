import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

import type { Dialog, Shell } from 'electron'

import type { DocumentArtifact, JobArtifactsState, PdfArtifactPayload } from '../shared/contracts.js'
import type { JobsConfig } from './jobs.js'

const MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
const ARTIFACT_ID = /^art_[A-Za-z0-9_-]{16,80}$/

interface ApiArtifact {
  artifact_id: string
  job_id: string
  document_key: DocumentArtifact['documentKey']
  document_label: string
  render_sequence: number
  source_revision: string
  artifact_revision: string
  media_type: DocumentArtifact['mediaType']
  sha256: string | null
  render_status: DocumentArtifact['renderStatus']
  filename: string | null
  failure_message: string | null
  created_at: string
  is_current: boolean
  is_last_successful: boolean
  is_approved: boolean
  preview_available: boolean
}

interface ApiArtifactList {
  job_id: string
  artifacts: ApiArtifact[]
  current_artifact_id: string | null
  last_successful_artifact_id: string | null
  approved_artifact_id: string | null
}

function toArtifact(value: ApiArtifact): DocumentArtifact {
  return {
    artifactId: value.artifact_id,
    jobId: value.job_id,
    documentKey: value.document_key,
    documentLabel: value.document_label,
    renderSequence: value.render_sequence,
    sourceRevision: value.source_revision,
    artifactRevision: value.artifact_revision,
    mediaType: value.media_type,
    sha256: value.sha256,
    renderStatus: value.render_status,
    filename: value.filename,
    failureMessage: value.failure_message,
    createdAt: value.created_at,
    isCurrent: value.is_current,
    isLastSuccessful: value.is_last_successful,
    isApproved: value.is_approved,
    previewAvailable: value.preview_available
  }
}

function toState(value: ApiArtifactList): JobArtifactsState {
  return {
    jobId: value.job_id,
    artifacts: value.artifacts.map(toArtifact),
    currentArtifactId: value.current_artifact_id,
    lastSuccessfulArtifactId: value.last_successful_artifact_id,
    approvedArtifactId: value.approved_artifact_id
  }
}

function safeId(value: string): string {
  if (!ARTIFACT_ID.test(value)) throw new Error('Invalid artifact')
  return value
}

function safeJobId(value: string): string {
  if (!value || value.length > 512 || /[\\/]/.test(value)) throw new Error('Invalid job')
  return value
}

function safeFilename(value: string | null, mediaType: string): string {
  const fallback = mediaType === 'application/pdf' ? 'resume.pdf' : 'resume.docx'
  if (!value) return fallback
  const name = path.basename(value).replace(/[^A-Za-z0-9._ -]/g, '_').slice(0, 180)
  return name || fallback
}

async function apiJson<T>(config: JobsConfig, route: string, method = 'GET'): Promise<T> {
  const response = await fetch(new URL(route, config.baseUrl), {
    method,
    headers: { Authorization: `Bearer ${config.deviceToken}` },
    redirect: 'error'
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string }
    throw new Error(body.detail ?? `Document request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

async function artifactBytes(config: JobsConfig, artifactId: string, preview: boolean) {
  const id = safeId(artifactId)
  const response = await fetch(
    new URL(`/v1/artifacts/${encodeURIComponent(id)}/${preview ? 'content' : 'download'}`, config.baseUrl),
    {
      headers: { Authorization: `Bearer ${config.deviceToken}` },
      redirect: 'error'
    }
  )
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string }
    throw new Error(body.detail ?? `Artifact unavailable (${response.status})`)
  }
  const contentLength = Number(response.headers.get('content-length') ?? 0)
  if (contentLength > MAX_ARTIFACT_BYTES) throw new Error('Artifact exceeds the desktop preview limit')
  const bytes = await response.arrayBuffer()
  if (bytes.byteLength > MAX_ARTIFACT_BYTES) throw new Error('Artifact exceeds the desktop preview limit')
  const hash = response.headers.get('x-content-sha256') ?? ''
  const computed = createHash('sha256').update(Buffer.from(bytes)).digest('hex')
  if (!/^[a-f0-9]{64}$/.test(hash) || hash !== computed) {
    throw new Error('Artifact bytes do not match the registered SHA-256')
  }
  if (response.headers.get('x-artifact-id') !== id) throw new Error('Artifact identity mismatch')
  const mediaType = (response.headers.get('content-type') ?? '').split(';')[0] ?? ''
  const encodedFilename = response.headers
    .get('content-disposition')
    ?.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (preview && mediaType !== 'application/pdf') throw new Error('Only PDF artifacts can be previewed')
  return {
    artifactId: id,
    artifactRevision: response.headers.get('x-artifact-revision') ?? '',
    sourceRevision: response.headers.get('x-source-revision') ?? '',
    sha256: hash,
    mediaType,
    filename: safeFilename(
      encodedFilename ? decodeURIComponent(encodedFilename) : null,
      mediaType
    ),
    bytes
  }
}

export function createMainDocumentsClient(
  config: JobsConfig,
  native: { dialog: Pick<Dialog, 'showSaveDialog'>, shell: Pick<Shell, 'openPath' | 'showItemInFolder'>, cacheRoot: string }
) {
  const downloadToCache = async (artifactId: string) => {
    const artifact = await artifactBytes(config, artifactId, false)
    await mkdir(native.cacheRoot, { recursive: true })
    const target = path.join(native.cacheRoot, `${artifact.artifactId}-${artifact.filename}`)
    await writeFile(target, Buffer.from(artifact.bytes), { mode: 0o600 })
    return target
  }
  return {
    async list(jobId: string): Promise<JobArtifactsState> {
      return toState(await apiJson<ApiArtifactList>(config, `/v1/jobs/${encodeURIComponent(safeJobId(jobId))}/artifacts`))
    },
    async refresh(jobId: string): Promise<JobArtifactsState> {
      return toState(await apiJson<ApiArtifactList>(config, `/v1/jobs/${encodeURIComponent(safeJobId(jobId))}/artifacts/refresh`, 'POST'))
    },
    async approve(jobId: string, artifactId: string): Promise<JobArtifactsState> {
      return toState(await apiJson<ApiArtifactList>(
        config,
        `/v1/jobs/${encodeURIComponent(safeJobId(jobId))}/artifacts/${encodeURIComponent(safeId(artifactId))}/approve`,
        'POST'
      ))
    },
    async loadPdf(artifactId: string): Promise<PdfArtifactPayload> {
      const artifact = await artifactBytes(config, artifactId, true)
      return {
        artifactId: artifact.artifactId,
        artifactRevision: artifact.artifactRevision,
        sourceRevision: artifact.sourceRevision,
        sha256: artifact.sha256,
        bytes: artifact.bytes
      }
    },
    async exportArtifact(artifactId: string): Promise<string> {
      const artifact = await artifactBytes(config, artifactId, false)
      const result = await native.dialog.showSaveDialog({ defaultPath: artifact.filename })
      if (result.canceled || !result.filePath) return 'Export cancelled'
      await writeFile(result.filePath, Buffer.from(artifact.bytes))
      return `Exported ${path.basename(result.filePath)}`
    },
    async reveal(artifactId: string): Promise<string> {
      const target = await downloadToCache(artifactId)
      native.shell.showItemInFolder(target)
      return `Revealed ${path.basename(target)}`
    },
    async open(artifactId: string): Promise<string> {
      const target = await downloadToCache(artifactId)
      const error = await native.shell.openPath(target)
      if (error) throw new Error(error)
      return `Opened ${path.basename(target)}`
    }
  }
}
