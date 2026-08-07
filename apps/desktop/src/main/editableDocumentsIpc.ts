import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type {
  ApplyEditableDocumentOperationsRequest,
  CreateEditableDocumentSnapshotRequest,
  DocumentKey,
  RestoreEditableDocumentSnapshotRequest,
  SaveEditableDocumentRequest
} from '../shared/editableDocuments.js'
import type { MainEditableDocumentsClient } from './editableDocuments.js'
import {
  safeDocumentKey,
  safeEditableArtifactId,
  safeEditableDocumentId,
  safeEditableJobId,
  safeEditableSnapshotId
} from './editableDocuments.js'

function objectRequest<T>(value: unknown, label: string): T {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`Invalid ${label}`)
  return value as T
}

export function registerEditableDocumentsIpc(
  ipc: Pick<IpcMain, 'handle'>,
  trusted: (event: IpcMainInvokeEvent) => MainEditableDocumentsClient
): void {
  ipc.handle('jobos:editable-documents:list', (event, jobId: unknown) => (
    trusted(event).list(safeEditableJobId(jobId))
  ))
  ipc.handle('jobos:editable-documents:get-for-job', (event, jobId: unknown, documentKey: unknown) => (
    trusted(event).getForJob(safeEditableJobId(jobId), safeDocumentKey(documentKey))
  ))
  ipc.handle('jobos:editable-documents:get', (event, documentId: unknown) => (
    trusted(event).get(safeEditableDocumentId(documentId))
  ))
  ipc.handle('jobos:editable-documents:create-blank', (
    event,
    jobId: unknown,
    documentKey: unknown,
    idempotencyKey: unknown
  ) => trusted(event).createBlank(
    safeEditableJobId(jobId),
    safeDocumentKey(documentKey),
    typeof idempotencyKey === 'string' ? idempotencyKey : ''
  ))
  ipc.handle('jobos:editable-documents:save', (event, documentId: unknown, rawRequest: unknown) => (
    trusted(event).save(
      safeEditableDocumentId(documentId),
      objectRequest<SaveEditableDocumentRequest>(rawRequest, 'editable document save')
    )
  ))
  ipc.handle('jobos:editable-documents:list-snapshots', (event, documentId: unknown) => (
    trusted(event).listSnapshots(safeEditableDocumentId(documentId))
  ))
  ipc.handle('jobos:editable-documents:create-snapshot', (event, documentId: unknown, rawRequest: unknown) => (
    trusted(event).createSnapshot(
      safeEditableDocumentId(documentId),
      objectRequest<CreateEditableDocumentSnapshotRequest>(rawRequest, 'document checkpoint')
    )
  ))
  ipc.handle('jobos:editable-documents:restore-snapshot', (
    event,
    documentId: unknown,
    snapshotId: unknown,
    rawRequest: unknown
  ) => trusted(event).restoreSnapshot(
    safeEditableDocumentId(documentId),
    safeEditableSnapshotId(snapshotId),
    objectRequest<RestoreEditableDocumentSnapshotRequest>(rawRequest, 'document restore')
  ))
  ipc.handle('jobos:editable-documents:apply-operations', (event, documentId: unknown, rawRequest: unknown) => (
    trusted(event).applyOperations(
      safeEditableDocumentId(documentId),
      objectRequest<ApplyEditableDocumentOperationsRequest>(rawRequest, 'document operations')
    )
  ))

  // Later-phase byte pipelines are intentionally explicit, typed no-ops in Phase 1.
  ipc.handle('jobos:editable-documents:import-registered', (
    event,
    jobId: unknown,
    documentKey: unknown,
    artifactId: unknown
  ) => trusted(event).importRegisteredArtifact(
    safeEditableJobId(jobId),
    safeDocumentKey(documentKey) as DocumentKey,
    safeEditableArtifactId(artifactId)
  ))
  ipc.handle('jobos:editable-documents:import-file', (event, jobId: unknown, documentKey: unknown) => (
    trusted(event).importExternalDocx(safeEditableJobId(jobId), safeDocumentKey(documentKey))
  ))
  ipc.handle('jobos:editable-documents:preview', (event, documentId: unknown) => (
    trusted(event).preview(safeEditableDocumentId(documentId))
  ))
  ipc.handle('jobos:editable-documents:export', (event, documentId: unknown, format: unknown) => {
    if (format !== 'docx' && format !== 'pdf') throw new Error('Invalid document export format')
    return trusted(event).exportGenerated(safeEditableDocumentId(documentId), format)
  })
  ipc.handle('jobos:editable-documents:publish', (event, documentId: unknown) => (
    trusted(event).publish(safeEditableDocumentId(documentId))
  ))
}
