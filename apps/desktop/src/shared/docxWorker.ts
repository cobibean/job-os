import type { DocumentCapabilities, DocumentContext, StructuredDocumentOperation } from '@jobos/docx-editor-core'

export type DocxWorkerRequest =
  | { kind: 'inspect'; bytes: ArrayBuffer }
  | { kind: 'apply'; bytes: ArrayBuffer; operations: StructuredDocumentOperation[] }

export type DocxWorkerResult =
  | { kind: 'inspect'; context: DocumentContext; capabilities: DocumentCapabilities }
  | { kind: 'apply'; bytes: ArrayBuffer; context: DocumentContext; capabilities: DocumentCapabilities }

export interface DocxWorkerEnvelope { requestId: string; request: DocxWorkerRequest }
export interface DocxWorkerResponse { requestId: string; result?: DocxWorkerResult; error?: string }
