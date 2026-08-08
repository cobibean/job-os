import path from 'node:path'

import type { DocumentContext, StructuredDocumentOperation } from '@jobos/docx-editor-core'
import { buildBlankDocx, parseDocx } from '@jobos/docx-engine'
import type { Dialog } from 'electron'

import {
  DOCX_DOCUMENT_LABELS,
  type DocxBinding,
  type DocxCapabilities,
  type DocxExternalChangeEvent,
  type DocxOpenResult,
  type DocxRecoveryEntry,
  type SaveDocxRequest,
  type SaveDocxResult
} from '../shared/docxDocuments.js'
import type { DocumentKey } from '../shared/editableDocuments.js'
import { DocxWorkerManager } from './DocxWorkerManager.js'
import { DocxFileStore } from './docxFileStore.js'
import { DocxFileWatcher } from './docxFileWatcher.js'
import { docxBindingId, LocalDocxBindingStore } from './localDocxBindingStore.js'

interface DocxDocumentsServiceOptions {
  dialog: Pick<Dialog, 'showOpenDialog' | 'showSaveDialog'>
  bindings: LocalDocxBindingStore
  files: DocxFileStore
  emit: (event: DocxExternalChangeEvent) => void
  worker?: DocxWorkerManager
}

function arrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}

function validJobId(value: string): string {
  if (
    !value
    || value.length > 512
    || value.includes('\\')
    || value.includes('/')
    || value.includes('\u0000')
  ) throw new Error('Invalid job')
  return value
}

function validDocumentKey(value: DocumentKey): DocumentKey {
  if (!Object.hasOwn(DOCX_DOCUMENT_LABELS, value)) throw new Error('Invalid document type')
  return value
}

async function capabilities(bytes: Uint8Array): Promise<DocxCapabilities> {
  const parsed = await parseDocx(bytes)
  const protectedBlockCount = parsed.blocks.filter(block => block.type === 'passthrough').length
  const editableBlockCount = parsed.blocks.length - protectedBlockCount
  const reasons: string[] = []
  if (protectedBlockCount) reasons.push(`${protectedBlockCount} complex item(s) are retained but not directly editable`)
  if (parsed.protection?.enforced && parsed.protection.edit && parsed.protection.edit !== 'none') {
    reasons.push(`Word editing restriction: ${parsed.protection.edit}`)
  }
  return {
    mode: editableBlockCount === 0
      ? 'read_only'
      : protectedBlockCount > 0 ? 'editable_with_protected_content' : 'editable',
    protectedBlockCount,
    editableBlockCount,
    reasons
  }
}

export class DocxDocumentsService {
  readonly #dialog: DocxDocumentsServiceOptions['dialog']
  readonly #bindings: LocalDocxBindingStore
  readonly #files: DocxFileStore
  readonly #watcher: DocxFileWatcher
  readonly #worker?: DocxWorkerManager

  constructor(options: DocxDocumentsServiceOptions) {
    this.#dialog = options.dialog
    this.#bindings = options.bindings
    this.#files = options.files
    this.#worker = options.worker
    this.#watcher = new DocxFileWatcher(options.files, options.emit)
  }

  async initialize(): Promise<void> {
    for (const binding of await this.#bindings.list()) this.#watcher.watch(binding)
  }

  dispose(): void { this.#watcher.dispose() }

  listBindings(jobId: string): Promise<DocxBinding[]> {
    return this.#bindings.list(validJobId(jobId))
  }

  async openBound(jobId: string, documentKey: DocumentKey): Promise<DocxOpenResult | null> {
    const binding = await this.#bindings.getForJob(validJobId(jobId), validDocumentKey(documentKey))
    return binding ? this.reload(binding.bindingId) : null
  }

  async chooseFile(jobId: string, documentKey: DocumentKey): Promise<DocxOpenResult | null> {
    const selection = await this.#dialog.showOpenDialog({
      title: `Choose the canonical ${DOCX_DOCUMENT_LABELS[validDocumentKey(documentKey)]} DOCX`,
      properties: ['openFile'],
      filters: [{ name: 'Word document', extensions: ['docx'] }]
    })
    const selected = selection.filePaths[0]
    return selection.canceled || !selected ? null : this.#bind(validJobId(jobId), documentKey, selected)
  }

  async createBlank(jobId: string, documentKey: DocumentKey): Promise<DocxOpenResult | null> {
    validJobId(jobId)
    validDocumentKey(documentKey)
    const label = DOCX_DOCUMENT_LABELS[documentKey]
    const selection = await this.#dialog.showSaveDialog({
      title: `Create ${label} DOCX`,
      defaultPath: `${label.replaceAll(' ', '-')}.docx`,
      filters: [{ name: 'Word document', extensions: ['docx'] }]
    })
    if (selection.canceled || !selection.filePath) return null
    const bytes = await buildBlankDocx()
    const bindingId = docxBindingId(jobId, documentKey)
    try {
      const current = await this.#files.read(selection.filePath)
      await this.#files.replace(bindingId, selection.filePath, current.sha256, bytes, 'manual')
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      await this.#files.writeNew(selection.filePath, bytes)
    }
    return this.#bind(jobId, documentKey, selection.filePath)
  }

  async reload(bindingId: string): Promise<DocxOpenResult> {
    const binding = await this.#requireBinding(bindingId)
    const file = await this.#files.read(binding.canonicalPath)
    const updated = await this.#updatedBinding(binding, file)
    await this.#bindings.put(updated)
    this.#watcher.update(updated)
    return { binding: updated, bytes: arrayBuffer(file.bytes) }
  }

  async save(request: SaveDocxRequest): Promise<SaveDocxResult> {
    if (!Number.isInteger(request.generation) || request.generation < 1) throw new Error('Invalid editor generation')
    if (!/^[a-f0-9]{64}$/.test(request.expectedSha256)) throw new Error('Invalid expected DOCX hash')
    const binding = await this.#requireBinding(request.bindingId)
    const bytes = new Uint8Array(request.bytes)
    const saved = await this.#files.replace(binding.bindingId, binding.canonicalPath, request.expectedSha256, bytes)
    const updated = await this.#updatedBinding(binding, saved.file)
    await this.#bindings.put(updated)
    this.#watcher.update(updated)
    return { binding: updated, persistedGeneration: request.generation, recoveryId: saved.recovery.recoveryId }
  }

  async saveAs(bindingId: string, bytesBuffer: ArrayBuffer): Promise<DocxOpenResult | null> {
    const binding = await this.#requireBinding(bindingId)
    const selection = await this.#dialog.showSaveDialog({
      title: 'Save DOCX as a copy',
      defaultPath: binding.filename,
      filters: [{ name: 'Word document', extensions: ['docx'] }]
    })
    if (selection.canceled || !selection.filePath) return null
    const bytes = new Uint8Array(bytesBuffer)
    try {
      const current = await this.#files.read(selection.filePath)
      await this.#files.replace(binding.bindingId, selection.filePath, current.sha256, bytes, 'manual')
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      await this.#files.writeNew(selection.filePath, bytes)
    }
    return this.#bind(binding.jobId, binding.documentKey, selection.filePath)
  }

  async createRecovery(bindingId: string, reason: DocxRecoveryEntry['reason']): Promise<DocxRecoveryEntry> {
    const binding = await this.#requireBinding(bindingId)
    const file = await this.#files.read(binding.canonicalPath)
    return this.#files.createRecovery(binding.bindingId, binding.canonicalPath, file.bytes, reason)
  }

  listRecoveries(bindingId: string): Promise<DocxRecoveryEntry[]> {
    return this.#files.listRecoveries(bindingId)
  }

  async restoreRecovery(bindingId: string, recoveryId: string): Promise<DocxOpenResult> {
    const binding = await this.#requireBinding(bindingId)
    const recovery = await this.#files.recoveryBytes(bindingId, recoveryId)
    const current = await this.#files.read(binding.canonicalPath)
    await this.#files.replace(bindingId, binding.canonicalPath, current.sha256, recovery, 'conflict')
    return this.reload(bindingId)
  }

  async inspect(jobId: string, documentKey: DocumentKey): Promise<{
    binding: DocxBinding
    context: DocumentContext
  }> {
    const worker = this.#worker
    if (!worker) throw new Error('DOCX worker unavailable')
    const binding = await this.#bindings.getForJob(validJobId(jobId), validDocumentKey(documentKey))
    if (!binding) throw new Error('DOCX is not bound on this device')
    const current = await this.#files.read(binding.canonicalPath)
    const observedBinding = current.sha256 === binding.sha256
      ? binding
      : await this.#updatedBinding(binding, current)
    if (observedBinding !== binding) {
      await this.#bindings.put(observedBinding)
      this.#watcher.update(observedBinding)
    }
    const result = await worker.run({ kind: 'inspect', bytes: arrayBuffer(current.bytes) })
    if (result.kind !== 'inspect') throw new Error('Unexpected DOCX worker result')
    return { binding: observedBinding, context: result.context }
  }

  async applyOperations(
    jobId: string,
    documentKey: DocumentKey,
    expectedSha256: string,
    operations: StructuredDocumentOperation[]
  ): Promise<{ binding: DocxBinding; context: DocumentContext; recoveryId: string }> {
    const worker = this.#worker
    if (!worker) throw new Error('DOCX worker unavailable')
    if (!/^[a-f0-9]{64}$/.test(expectedSha256)) throw new Error('Invalid expected DOCX hash')
    const binding = await this.#bindings.getForJob(validJobId(jobId), validDocumentKey(documentKey))
    if (!binding) throw new Error('DOCX is not bound on this device')
    const current = await this.#files.read(binding.canonicalPath)
    if (current.sha256 !== expectedSha256) throw new Error('DOCX changed outside JobOS')
    const result = await worker.run({ kind: 'apply', bytes: arrayBuffer(current.bytes), operations })
    if (result.kind !== 'apply') throw new Error('Unexpected DOCX worker result')
    const saved = await this.#files.replace(
      binding.bindingId,
      binding.canonicalPath,
      expectedSha256,
      new Uint8Array(result.bytes),
      'agent'
    )
    const updated = await this.#updatedBinding(binding, saved.file)
    await this.#bindings.put(updated)
    this.#watcher.update(updated)
    return { binding: updated, context: result.context, recoveryId: saved.recovery.recoveryId }
  }

  async unbind(bindingId: string): Promise<void> {
    this.#watcher.unwatch(bindingId)
    await this.#bindings.remove(bindingId)
  }

  async #bind(jobId: string, documentKey: DocumentKey, canonicalPath: string): Promise<DocxOpenResult> {
    const file = await this.#files.read(canonicalPath)
    const now = new Date().toISOString()
    const previous = await this.#bindings.getForJob(jobId, documentKey)
    const binding: DocxBinding = {
      schemaVersion: 1,
      bindingId: docxBindingId(jobId, documentKey),
      jobId,
      documentKey,
      documentLabel: DOCX_DOCUMENT_LABELS[documentKey],
      canonicalPath: path.resolve(canonicalPath),
      filename: path.basename(canonicalPath),
      sha256: file.sha256,
      byteLength: file.byteLength,
      modifiedAtMs: file.modifiedAtMs,
      revision: (previous?.revision ?? 0) + 1,
      capabilities: await capabilities(file.bytes),
      createdAt: previous?.createdAt ?? now,
      updatedAt: now
    }
    await this.#bindings.put(binding)
    this.#watcher.update(binding)
    if (!previous || previous.canonicalPath !== binding.canonicalPath) {
      await this.#files.createRecovery(binding.bindingId, binding.canonicalPath, file.bytes, 'baseline')
    }
    return { binding, bytes: arrayBuffer(file.bytes) }
  }

  async #updatedBinding(binding: DocxBinding, file: Awaited<ReturnType<DocxFileStore['read']>>): Promise<DocxBinding> {
    return {
      ...binding,
      sha256: file.sha256,
      byteLength: file.byteLength,
      modifiedAtMs: file.modifiedAtMs,
      revision: binding.revision + 1,
      capabilities: await capabilities(file.bytes),
      updatedAt: new Date().toISOString()
    }
  }

  async #requireBinding(bindingId: string): Promise<DocxBinding> {
    if (!/^docx_[a-f0-9]{24}$/.test(bindingId)) throw new Error('Invalid DOCX binding')
    const binding = await this.#bindings.get(bindingId)
    if (!binding) throw new Error('DOCX binding not found')
    return binding
  }
}
