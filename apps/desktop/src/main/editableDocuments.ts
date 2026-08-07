import { createHash, randomUUID } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'

import type { Dialog } from 'electron'

import type {
  ApplyEditableDocumentOperationsRequest,
  CreateEditableDocumentSnapshotRequest,
  DocumentComment,
  DocumentImportReport,
  DocumentKey,
  DocumentSettings,
  EditableDocument,
  EditableDocumentExportResult,
  EditableDocumentPreview,
  EditableDocumentSnapshot,
  EditableDocumentSummary,
  JobHunterOperation,
  OperationReceipt,
  RestoreEditableDocumentSnapshotRequest,
  SaveEditableDocumentRequest,
  TiptapDocumentJson
} from '../shared/editableDocuments.js'
import { unresolvedSuggestionCount, validateEditableContent } from '../shared/editableDocumentSchema.js'
import { exportEditableDocumentDocx } from './document-export/documentDocx.js'
import { exportEditableDocumentPdf } from './document-export/pdfExporter.js'
import { importDocx } from './document-import/docxImporter.js'
import { loadVerifiedArtifactBytes } from './documents.js'
import type { JobsConfig } from './jobs.js'

const DOCUMENT_ID = /^edoc_[A-Za-z0-9_-]{24}$/
const SNAPSHOT_ID = /^dsnap_[A-Za-z0-9_-]{24}$/
const ARTIFACT_ID = /^art_[A-Za-z0-9_-]{16,80}$/
const NODE_ID = /^node_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const IDEMPOTENCY_KEY = /^[A-Za-z0-9._:-]{1,128}$/
const DOCUMENT_KEYS = new Set<DocumentKey>(['resume', 'cover_letter', 'references'])
const SEMANTIC_ROLES = new Set([
  'contact', 'summary', 'experience', 'experience_achievement', 'education', 'skills',
  'reference', 'cover_letter_body', 'closing', 'custom'
])

interface EditableDocumentsNative {
  dialog: Pick<Dialog, 'showOpenDialog' | 'showSaveDialog'> & Partial<Pick<Dialog, 'showMessageBox'>>
}

async function confirmDroppedImport(
  imported: Awaited<ReturnType<typeof importDocx>>,
  native: EditableDocumentsNative | undefined
): Promise<boolean> {
  const dropped = imported.importReport.issues.filter(issue => issue.severity === 'dropped')
  if (dropped.length === 0) return true
  if (!native?.dialog.showMessageBox) throw new Error('Import confirmation is unavailable')
  const result = await native.dialog.showMessageBox({
    type: 'warning',
    buttons: ['Import anyway', 'Cancel'],
    defaultId: 1,
    cancelId: 1,
    title: 'Some Word content could not be imported',
    message: `${dropped.reduce((total, issue) => total + issue.count, 0)} item(s) will be dropped.`,
    detail: dropped.map(issue => `• ${issue.message}`).join('\n').slice(0, 2_000),
    noLink: true
  })
  return result.response === 0
}


interface ApiSettings {
  page_size: DocumentSettings['pageSize']
  orientation: 'portrait'
  margins_inches: DocumentSettings['marginsInches']
  default_font_family: DocumentSettings['defaultFontFamily']
  default_font_size_pt: number
  header: { left: string; center: string; right: string; first_page_different: boolean }
  footer: { left: string; center: string; right: string; first_page_different: boolean }
  show_page_numbers: boolean
}

interface ApiComment {
  comment_id: string
  block_id: string
  author: DocumentComment['author']
  body: string
  created_at: string
  resolved_at: string | null
}

interface ApiImportReport {
  source_filename: string | null
  imported_at: string | null
  issues: Array<{ code: string; severity: 'normalized' | 'dropped'; message: string; count: number }>
}

interface ApiDocument {
  schema_version: 1
  document_id: string
  job_id: string
  document_key: DocumentKey
  document_label: EditableDocument['documentLabel']
  revision: number
  content: TiptapDocumentJson
  settings: ApiSettings
  comments: ApiComment[]
  source_artifact_id: string | null
  source_filename: string | null
  source_sha256: string | null
  published_revision: number | null
  import_report: ApiImportReport
  created_at: string
  updated_at: string
}

interface ApiSummary {
  document_id: string
  job_id: string
  document_key: DocumentKey
  document_label: EditableDocument['documentLabel']
  revision: number
  source_artifact_id: string | null
  published_revision: number | null
  created_at: string
  updated_at: string
}

interface ApiSnapshot {
  snapshot_id: string
  document_id: string
  document_revision: number
  reason: EditableDocumentSnapshot['reason']
  actor: EditableDocumentSnapshot['actor']
  label: string | null
  created_at: string
}

interface ApiOperationReceipt {
  document: ApiDocument
  changed_block_ids: string[]
  changes: Array<{ block_id: string; before: string; after: string }>
  snapshot_id: string
}

function safeString(value: unknown, label: string, maximum = 512): string {
  if (typeof value !== 'string' || !value || value.length > maximum) throw new Error(`Invalid ${label}`)
  return value
}

export function safeEditableJobId(value: unknown): string {
  const id = safeString(value, 'job')
  const unsafe = Array.from(id).some(character => (
    character === '/' || character === '\\' || character.charCodeAt(0) < 32
  ))
  if (unsafe) throw new Error('Invalid job')
  return id
}

export function safeEditableDocumentId(value: unknown): `edoc_${string}` {
  if (typeof value !== 'string' || !DOCUMENT_ID.test(value)) throw new Error('Invalid editable document')
  return value as `edoc_${string}`
}

export function safeEditableSnapshotId(value: unknown): `dsnap_${string}` {
  if (typeof value !== 'string' || !SNAPSHOT_ID.test(value)) throw new Error('Invalid document snapshot')
  return value as `dsnap_${string}`
}

export function safeEditableArtifactId(value: unknown): string {
  if (typeof value !== 'string' || !ARTIFACT_ID.test(value)) throw new Error('Invalid artifact')
  return value
}

export function safeDocumentKey(value: unknown): DocumentKey {
  if (typeof value !== 'string' || !DOCUMENT_KEYS.has(value as DocumentKey)) throw new Error('Invalid document type')
  return value as DocumentKey
}

function safeIdempotencyKey(value: unknown): string {
  if (typeof value !== 'string' || !IDEMPOTENCY_KEY.test(value)) throw new Error('Invalid idempotency key')
  return value
}

function safeRevision(value: unknown): number {
  if (!Number.isInteger(value) || Number(value) < 1) throw new Error('Invalid document revision')
  return Number(value)
}

function toSettings(value: ApiSettings): DocumentSettings {
  return {
    pageSize: value.page_size,
    orientation: value.orientation,
    marginsInches: value.margins_inches,
    defaultFontFamily: value.default_font_family,
    defaultFontSizePt: value.default_font_size_pt,
    header: {
      left: value.header.left,
      center: value.header.center,
      right: value.header.right,
      firstPageDifferent: value.header.first_page_different
    },
    footer: {
      left: value.footer.left,
      center: value.footer.center,
      right: value.footer.right,
      firstPageDifferent: value.footer.first_page_different
    },
    showPageNumbers: value.show_page_numbers
  }
}

function fromSettings(value: DocumentSettings): ApiSettings {
  return {
    page_size: value.pageSize,
    orientation: value.orientation,
    margins_inches: value.marginsInches,
    default_font_family: value.defaultFontFamily,
    default_font_size_pt: value.defaultFontSizePt,
    header: {
      left: value.header.left,
      center: value.header.center,
      right: value.header.right,
      first_page_different: value.header.firstPageDifferent
    },
    footer: {
      left: value.footer.left,
      center: value.footer.center,
      right: value.footer.right,
      first_page_different: value.footer.firstPageDifferent
    },
    show_page_numbers: value.showPageNumbers
  }
}

function toComment(value: ApiComment): DocumentComment {
  return {
    commentId: value.comment_id as `comment_${string}`,
    blockId: value.block_id as `node_${string}`,
    author: value.author,
    body: value.body,
    createdAt: value.created_at,
    resolvedAt: value.resolved_at
  }
}

function fromComment(value: DocumentComment): ApiComment {
  return {
    comment_id: value.commentId,
    block_id: value.blockId,
    author: value.author,
    body: value.body,
    created_at: value.createdAt,
    resolved_at: value.resolvedAt
  }
}

function toImportReport(value: ApiImportReport): DocumentImportReport {
  return {
    sourceFilename: value.source_filename,
    importedAt: value.imported_at,
    issues: value.issues
  }
}

function toDocument(value: ApiDocument): EditableDocument {
  const document: EditableDocument = {
    schemaVersion: value.schema_version,
    documentId: safeEditableDocumentId(value.document_id),
    jobId: safeEditableJobId(value.job_id),
    documentKey: safeDocumentKey(value.document_key),
    documentLabel: value.document_label,
    revision: safeRevision(value.revision),
    content: value.content,
    settings: toSettings(value.settings),
    comments: value.comments.map(toComment),
    sourceArtifactId: value.source_artifact_id === null ? null : safeEditableArtifactId(value.source_artifact_id),
    sourceFilename: value.source_filename,
    sourceSha256: value.source_sha256,
    publishedRevision: value.published_revision,
    importReport: toImportReport(value.import_report),
    createdAt: value.created_at,
    updatedAt: value.updated_at
  }
  if (document.schemaVersion !== 1) throw new Error('Unsupported editable document schema')
  if (!['Resume', 'Cover Letter', 'References'].includes(document.documentLabel)) throw new Error('Invalid document label')
  if (document.publishedRevision !== null) safeRevision(document.publishedRevision)
  if (document.sourceSha256 !== null && !/^[a-f0-9]{64}$/.test(document.sourceSha256)) throw new Error('Invalid document source checksum')
  validateEditableContent(document.content, document.settings, document.comments, document.importReport)
  return document
}

function toSummary(value: ApiSummary): EditableDocumentSummary {
  return {
    documentId: safeEditableDocumentId(value.document_id),
    jobId: safeEditableJobId(value.job_id),
    documentKey: safeDocumentKey(value.document_key),
    documentLabel: value.document_label,
    revision: safeRevision(value.revision),
    sourceArtifactId: value.source_artifact_id === null ? null : safeEditableArtifactId(value.source_artifact_id),
    publishedRevision: value.published_revision === null ? null : safeRevision(value.published_revision),
    createdAt: value.created_at,
    updatedAt: value.updated_at
  }
}

function toSnapshot(value: ApiSnapshot): EditableDocumentSnapshot {
  return {
    snapshotId: safeEditableSnapshotId(value.snapshot_id),
    documentId: safeEditableDocumentId(value.document_id),
    documentRevision: safeRevision(value.document_revision),
    reason: value.reason,
    actor: value.actor,
    label: value.label,
    createdAt: value.created_at
  }
}

function operationPayload(operation: JobHunterOperation): Record<string, unknown> {
  if ('blockId' in operation && !NODE_ID.test(operation.blockId)) throw new Error('Invalid operation block')
  if ('afterBlockId' in operation && !NODE_ID.test(operation.afterBlockId)) throw new Error('Invalid operation block')
  if ('semanticRole' in operation && !SEMANTIC_ROLES.has(operation.semanticRole)) throw new Error('Invalid semantic role')
  switch (operation.type) {
    case 'replace_block_text':
      return { type: operation.type, block_id: operation.blockId, expected_text: operation.expectedText, replacement_text: operation.replacementText }
    case 'insert_block_after':
      return { type: operation.type, after_block_id: operation.afterBlockId, node_type: operation.nodeType, semantic_role: operation.semanticRole, text: operation.text }
    case 'delete_block':
      return { type: operation.type, block_id: operation.blockId, expected_text: operation.expectedText }
    case 'move_block_after':
      return { type: operation.type, block_id: operation.blockId, after_block_id: operation.afterBlockId }
    case 'set_block_role':
      return { type: operation.type, block_id: operation.blockId, semantic_role: operation.semanticRole }
  }
}

function validateOperationRequest(request: ApplyEditableDocumentOperationsRequest): void {
  safeRevision(request.baseRevision)
  safeIdempotencyKey(request.idempotencyKey)
  if (!Array.isArray(request.operations) || request.operations.length < 1 || request.operations.length > 50) throw new Error('Invalid document operations')
  let textCharacters = 0
  for (const operation of request.operations) {
    const payload = operationPayload(operation)
    for (const key of ['expected_text', 'replacement_text', 'text']) {
      const value = payload[key]
      if (value !== undefined && (typeof value !== 'string' || value.length > 20_000)) throw new Error('Invalid operation text')
    }
    textCharacters += typeof payload.replacement_text === 'string' ? payload.replacement_text.length : 0
    textCharacters += typeof payload.text === 'string' ? payload.text.length : 0
  }
  if (textCharacters > 20_000) throw new Error('Operation text exceeds 20,000 characters')
}

function errorMessage(status: number, value: unknown): string {
  let detail: unknown
  if (value && typeof value === 'object') detail = (value as { detail?: unknown }).detail
  if (detail && typeof detail === 'object') detail = (detail as { message?: unknown }).message
  if (typeof detail === 'string' && detail.length > 0 && detail.length <= 500 && !/[\\/](?:Users|home|tmp)[\\/]/i.test(detail)) return detail
  return `Editable document request failed (${status})`
}

async function apiJson<T>(config: JobsConfig, route: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(new URL(route, config.baseUrl), {
    ...init,
    headers: {
      Authorization: ['Bearer', config.deviceToken].join(' '),
      ...(init.body === undefined ? {} : { 'Content-Type': 'application/json' })
    },
    redirect: 'error'
  })
  const value = await response.json().catch(() => null) as unknown
  if (!response.ok) throw new Error(errorMessage(response.status, value))
  if (!value || typeof value !== 'object') throw new Error('Editable document response was invalid')
  return value as T
}

function jsonBody(value: unknown): string {
  return JSON.stringify(value)
}


function apiImportReport(report: DocumentImportReport): ApiImportReport {
  return {
    source_filename: report.sourceFilename,
    imported_at: report.importedAt,
    issues: report.issues
  }
}

async function persistImport(
  config: JobsConfig,
  jobId: string,
  documentKey: DocumentKey,
  source: Record<string, unknown>
): Promise<EditableDocument> {
  const summaries = await apiJson<{ documents: ApiSummary[] }>(
    config,
    `/v1/jobs/${encodeURIComponent(safeEditableJobId(jobId))}/editable-documents`
  )
  const current = summaries.documents.find(item => item.document_key === documentKey)
  const idempotencyKey = `desktop-import-${randomUUID()}`
  if (current) {
    return toDocument(await apiJson<ApiDocument>(
      config,
      `/v1/editable-documents/${encodeURIComponent(safeEditableDocumentId(current.document_id))}/import`,
      {
        method: 'POST',
        body: jsonBody({
          base_revision: current.revision,
          source,
          idempotency_key: idempotencyKey
        })
      }
    ))
  }
  return toDocument(await apiJson<ApiDocument>(
    config,
    `/v1/jobs/${encodeURIComponent(safeEditableJobId(jobId))}/editable-documents`,
    {
      method: 'POST',
      body: jsonBody({ ...source, idempotency_key: idempotencyKey })
    }
  ))
}

async function loadEditableDocument(config: JobsConfig, documentId: string): Promise<EditableDocument> {
  return toDocument(await apiJson<ApiDocument>(
    config,
    `/v1/editable-documents/${encodeURIComponent(safeEditableDocumentId(documentId))}`
  ))
}

function generatedFilename(document: EditableDocument, extension: 'docx' | 'pdf'): string {
  const stem = document.documentLabel
    .normalize('NFKD')
    .replace(/[^A-Za-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 60) || document.documentKey
  return `${stem}-r${document.revision}.${extension}`
}

function exactArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}

export function createMainEditableDocumentsClient(
  config: JobsConfig,
  native?: EditableDocumentsNative
) {
  const pdfCache = new Map<string, { revision: number; bytes: Uint8Array }>()
  const pdfFor = async (
    document: EditableDocument,
    allowUnresolvedSuggestions = false
  ): Promise<Uint8Array> => {
    const hasUnresolvedSuggestions = unresolvedSuggestionCount(document.content) > 0
    if (hasUnresolvedSuggestions && allowUnresolvedSuggestions) {
      return exportEditableDocumentPdf(document, { allowUnresolvedSuggestions: true })
    }
    if (hasUnresolvedSuggestions) {
      throw new Error('Resolve every suggestion before export or publication')
    }
    const cached = pdfCache.get(document.documentId)
    if (cached?.revision === document.revision) return cached.bytes
    const bytes = await exportEditableDocumentPdf(document)
    pdfCache.set(document.documentId, { revision: document.revision, bytes })
    return bytes
  }

  return {
    async list(jobId: string): Promise<EditableDocumentSummary[]> {
      const owner = safeEditableJobId(jobId)
      const result = await apiJson<{ documents: ApiSummary[] }>(config, `/v1/jobs/${encodeURIComponent(owner)}/editable-documents`)
      if (!Array.isArray(result.documents)) throw new Error('Editable document list was invalid')
      return result.documents.map(toSummary)
    },
    async getForJob(jobId: string, documentKey: DocumentKey): Promise<EditableDocument> {
      const owner = safeEditableJobId(jobId)
      const key = safeDocumentKey(documentKey)
      return toDocument(await apiJson<ApiDocument>(config, `/v1/jobs/${encodeURIComponent(owner)}/editable-documents/${key}`))
    },
    async get(documentId: string): Promise<EditableDocument> {
      const id = safeEditableDocumentId(documentId)
      return toDocument(await apiJson<ApiDocument>(config, `/v1/editable-documents/${id}`))
    },
    async createBlank(jobId: string, documentKey: DocumentKey, idempotencyKey: string): Promise<EditableDocument> {
      const owner = safeEditableJobId(jobId)
      const key = safeDocumentKey(documentKey)
      const idempotency = safeIdempotencyKey(idempotencyKey)
      return toDocument(await apiJson<ApiDocument>(config, `/v1/jobs/${encodeURIComponent(owner)}/editable-documents`, {
        method: 'POST',
        body: jsonBody({ mode: 'blank', document_key: key, idempotency_key: idempotency })
      }))
    },
    async save(documentId: string, request: SaveEditableDocumentRequest): Promise<EditableDocument> {
      const id = safeEditableDocumentId(documentId)
      const baseRevision = safeRevision(request.baseRevision)
      const idempotencyKey = safeIdempotencyKey(request.idempotencyKey)
      validateEditableContent(request.content, request.settings, request.comments)
      return toDocument(await apiJson<ApiDocument>(config, `/v1/editable-documents/${id}`, {
        method: 'PUT',
        body: jsonBody({
          base_revision: baseRevision,
          content: request.content,
          settings: fromSettings(request.settings),
          comments: request.comments.map(fromComment),
          idempotency_key: idempotencyKey
        })
      }))
    },
    async listSnapshots(documentId: string): Promise<EditableDocumentSnapshot[]> {
      const id = safeEditableDocumentId(documentId)
      const result = await apiJson<{ snapshots: ApiSnapshot[] }>(config, `/v1/editable-documents/${id}/snapshots`)
      if (!Array.isArray(result.snapshots)) throw new Error('Document snapshot list was invalid')
      return result.snapshots.map(toSnapshot)
    },
    async createSnapshot(documentId: string, request: CreateEditableDocumentSnapshotRequest): Promise<EditableDocumentSnapshot> {
      const id = safeEditableDocumentId(documentId)
      const baseRevision = safeRevision(request.baseRevision)
      const idempotencyKey = safeIdempotencyKey(request.idempotencyKey)
      if (request.reason !== 'manual' || typeof request.label !== 'string' || !request.label || request.label.length > 120) throw new Error('Invalid document checkpoint')
      return toSnapshot(await apiJson<ApiSnapshot>(config, `/v1/editable-documents/${id}/snapshots`, {
        method: 'POST',
        body: jsonBody({ base_revision: baseRevision, reason: 'manual', label: request.label, idempotency_key: idempotencyKey })
      }))
    },
    async restoreSnapshot(documentId: string, snapshotId: string, request: RestoreEditableDocumentSnapshotRequest): Promise<EditableDocument> {
      const id = safeEditableDocumentId(documentId)
      const snapshot = safeEditableSnapshotId(snapshotId)
      return toDocument(await apiJson<ApiDocument>(config, `/v1/editable-documents/${id}/snapshots/${snapshot}/restore`, {
        method: 'POST',
        body: jsonBody({ base_revision: safeRevision(request.baseRevision), idempotency_key: safeIdempotencyKey(request.idempotencyKey) })
      }))
    },
    async applyOperations(documentId: string, request: ApplyEditableDocumentOperationsRequest): Promise<OperationReceipt> {
      const id = safeEditableDocumentId(documentId)
      validateOperationRequest(request)
      const result = await apiJson<ApiOperationReceipt>(config, `/v1/editable-documents/${id}/operations`, {
        method: 'POST',
        body: jsonBody({
          base_revision: request.baseRevision,
          operations: request.operations.map(operationPayload),
          origin: 'user',
          idempotency_key: request.idempotencyKey
        })
      })
      const snapshotId = safeEditableSnapshotId(result.snapshot_id)
      const document = toDocument(result.document)
      if (!Array.isArray(result.changed_block_ids) || !Array.isArray(result.changes)) throw new Error('Document operation receipt was invalid')
      const changedBlockIds = result.changed_block_ids.map(value => {
        if (!NODE_ID.test(value)) throw new Error('Document operation receipt was invalid')
        return value as `node_${string}`
      })
      return {
        document,
        changedBlockIds,
        changes: result.changes.map(change => {
          if (!NODE_ID.test(change.block_id) || typeof change.before !== 'string' || typeof change.after !== 'string') throw new Error('Document operation receipt was invalid')
          return { blockId: change.block_id as `node_${string}`, before: change.before, after: change.after }
        }),
        snapshotId
      }
    },
    async importRegisteredArtifact(
      jobId: string,
      documentKey: DocumentKey,
      artifactId: string
    ): Promise<EditableDocument> {
      const owner = safeEditableJobId(jobId)
      if (!DOCUMENT_KEYS.has(documentKey)) throw new Error('Invalid document key')
      const artifact = await loadVerifiedArtifactBytes(config, safeEditableArtifactId(artifactId), false)
      if (artifact.mediaType !== 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') {
        throw new Error('Only DOCX artifacts can be imported')
      }
      const imported = await importDocx(
        new Uint8Array(artifact.bytes),
        artifact.filename,
        documentKey
      )
      if (!(await confirmDroppedImport(imported, native))) throw new Error('Import cancelled')
      return persistImport(config, owner, documentKey, {
        mode: 'import_registered_artifact',
        document_key: documentKey,
        source_artifact_id: artifact.artifactId,
        content: imported.content,
        settings: fromSettings(imported.settings),
        import_report: apiImportReport(imported.importReport)
      })
    },
    async importExternalDocx(
      jobId: string,
      documentKey: DocumentKey
    ): Promise<EditableDocument | null> {
      const owner = safeEditableJobId(jobId)
      if (!DOCUMENT_KEYS.has(documentKey)) throw new Error('Invalid document key')
      if (!native) throw new Error('Native DOCX import is unavailable')
      const selection = await native.dialog.showOpenDialog({
        title: `Import ${documentKey.replace('_', ' ')} DOCX`,
        properties: ['openFile'],
        filters: [{ name: 'Word document', extensions: ['docx'] }]
      })
      const [selectedPath] = selection.filePaths
      if (selection.canceled || !selectedPath || selection.filePaths.length !== 1) return null
      const filename = path.basename(selectedPath)
      if (filename !== selectedPath.split(path.sep).at(-1) || !filename.toLowerCase().endsWith('.docx')) {
        throw new Error('Selected file must be a DOCX')
      }
      const bytes = new Uint8Array(await readFile(selectedPath))
      const imported = await importDocx(bytes, filename, documentKey)
      if (!(await confirmDroppedImport(imported, native))) return null
      return persistImport(config, owner, documentKey, {
        mode: 'import_external_docx',
        document_key: documentKey,
        source_filename: filename,
        source_base64: Buffer.from(bytes).toString('base64'),
        source_sha256: createHash('sha256').update(bytes).digest('hex'),
        content: imported.content,
        settings: fromSettings(imported.settings),
        import_report: apiImportReport(imported.importReport)
      })
    },
    async preview(documentId: string): Promise<EditableDocumentPreview> {
      const document = await loadEditableDocument(config, documentId)
      const bytes = await pdfFor(document, true)
      return {
        documentId: document.documentId,
        revision: document.revision,
        filename: generatedFilename(document, 'pdf'),
        sha256: createHash('sha256').update(bytes).digest('hex'),
        bytes: exactArrayBuffer(bytes)
      }
    },
    async exportGenerated(
      documentId: string,
      format: 'docx' | 'pdf'
    ): Promise<EditableDocumentExportResult> {
      if (!native) throw new Error('Native document export is unavailable')
      const document = await loadEditableDocument(config, documentId)
      const bytes = format === 'docx'
        ? await exportEditableDocumentDocx(document)
        : await pdfFor(document)
      const filename = generatedFilename(document, format)
      const selection = await native.dialog.showSaveDialog({
        title: `Export ${document.documentLabel}`,
        defaultPath: filename,
        filters: [{
          name: format === 'docx' ? 'Word document' : 'PDF document',
          extensions: [format]
        }]
      })
      if (selection.canceled || !selection.filePath) {
        return { cancelled: true, filename: null, message: 'Export cancelled' }
      }
      await writeFile(selection.filePath, bytes, { flag: 'wx' })
      return {
        cancelled: false,
        filename: path.basename(selection.filePath),
        message: `${format.toUpperCase()} exported`
      }
    },
    async publish(documentId: string): Promise<EditableDocument> {
      const document = await loadEditableDocument(config, documentId)
      const docx = await exportEditableDocumentDocx(document)
      const pdf = await pdfFor(document)
      return toDocument(await apiJson<ApiDocument>(
        config,
        `/v1/editable-documents/${encodeURIComponent(document.documentId)}/publish`,
        {
          method: 'POST',
          body: jsonBody({
            expected_revision: document.revision,
            docx_filename: generatedFilename(document, 'docx'),
            docx_base64: Buffer.from(docx).toString('base64'),
            docx_sha256: createHash('sha256').update(docx).digest('hex'),
            pdf_filename: generatedFilename(document, 'pdf'),
            pdf_base64: Buffer.from(pdf).toString('base64'),
            pdf_sha256: createHash('sha256').update(pdf).digest('hex'),
            idempotency_key: `desktop-publish-${document.documentId}-${document.revision}`
          })
        }
      ))
    }
  }
}

export type MainEditableDocumentsClient = ReturnType<typeof createMainEditableDocumentsClient>
