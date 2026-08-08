import type { DocumentKey } from './editableDocuments.js'

export type DocxDocumentLabel = 'Resume' | 'Cover Letter' | 'References'
export type DocxCapabilityMode = 'editable' | 'editable_with_protected_content' | 'read_only'

export interface DocxCapabilities {
  mode: DocxCapabilityMode
  protectedBlockCount: number
  editableBlockCount: number
  reasons: string[]
}

export interface DocxBinding {
  schemaVersion: 1
  bindingId: string
  jobId: string
  documentKey: DocumentKey
  documentLabel: DocxDocumentLabel
  canonicalPath: string
  filename: string
  sha256: string
  byteLength: number
  modifiedAtMs: number
  revision: number
  capabilities: DocxCapabilities
  createdAt: string
  updatedAt: string
}

export interface DocxOpenResult {
  binding: DocxBinding
  bytes: ArrayBuffer
}

export interface SaveDocxRequest {
  bindingId: string
  expectedSha256: string
  generation: number
  bytes: ArrayBuffer
}

export interface SaveDocxResult {
  binding: DocxBinding
  persistedGeneration: number
  recoveryId: string
}

export interface DocxRecoveryEntry {
  recoveryId: string
  bindingId: string
  filename: string
  sha256: string
  byteLength: number
  reason: 'baseline' | 'autosave' | 'manual' | 'conflict' | 'agent'
  createdAt: string
}

export type DocxExternalChangeEvent =
  | { bindingId: string; jobId: string; documentKey: DocumentKey; kind: 'changed'; sha256: string; modifiedAtMs: number }
  | { bindingId: string; jobId: string; documentKey: DocumentKey; kind: 'missing' }

export interface DocxDocumentsBridge {
  listBindings: (jobId: string) => Promise<DocxBinding[]>
  openBound: (jobId: string, documentKey: DocumentKey) => Promise<DocxOpenResult | null>
  openArtifact: (jobId: string, documentKey: DocumentKey, artifactId: string) => Promise<DocxOpenResult>
  chooseFile: (jobId: string, documentKey: DocumentKey) => Promise<DocxOpenResult | null>
  createBlank: (jobId: string, documentKey: DocumentKey) => Promise<DocxOpenResult | null>
  reload: (bindingId: string) => Promise<DocxOpenResult>
  save: (request: SaveDocxRequest) => Promise<SaveDocxResult>
  saveAs: (bindingId: string, bytes: ArrayBuffer) => Promise<DocxOpenResult | null>
  createRecovery: (bindingId: string, reason: DocxRecoveryEntry['reason']) => Promise<DocxRecoveryEntry>
  listRecoveries: (bindingId: string) => Promise<DocxRecoveryEntry[]>
  restoreRecovery: (bindingId: string, recoveryId: string) => Promise<DocxOpenResult>
  unbind: (bindingId: string) => Promise<void>
  subscribe: (listener: (event: DocxExternalChangeEvent) => void) => () => void
}

export const DOCX_DOCUMENT_LABELS: Record<DocumentKey, DocxDocumentLabel> = {
  resume: 'Resume',
  cover_letter: 'Cover Letter',
  references: 'References'
}
