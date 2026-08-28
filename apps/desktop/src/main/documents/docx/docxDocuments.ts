import { mkdir } from 'node:fs/promises'
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
} from '../../../shared/docxDocuments.js'
import type { DocumentKey } from '../../../shared/editableDocuments.js'
import { DocxWorkerManager } from './DocxWorkerManager.js'
import { DocxFileStore, sha256 } from './docxFileStore.js'
import { DocxFileWatcher } from './docxFileWatcher.js'
import { docxBindingId, LocalDocxBindingStore } from './localDocxBindingStore.js'

interface DocxDocumentsServiceOptions {
  dialog: Pick<Dialog, 'showOpenDialog' | 'showSaveDialog'>
  bindings: LocalDocxBindingStore
  files: DocxFileStore
  artifactRoot: string
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

interface DocxArtifactSource {
  filename: string
  sha256: string
  bytes: ArrayBuffer
}

function canonicalArtifactFilename(value: string): string {
  const basename = path.basename(value).replace(/[^A-Za-z0-9._ ()-]/g, '_').slice(0, 96)
  const filename = basename || 'JobOS-document.docx'
  return filename.toLowerCase().endsWith('.docx') ? filename : `${filename}.docx`
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
  readonly #artifactRoot: string
  readonly #watcher: DocxFileWatcher
  readonly #worker?: DocxWorkerManager
  readonly #bindingQueues = new Map<string, Promise<void>>()

  constructor(options: DocxDocumentsServiceOptions) {
    this.#dialog = options.dialog
    this.#bindings = options.bindings
    this.#files = options.files
    this.#artifactRoot = path.resolve(options.artifactRoot)
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

  async openArtifact(
    jobId: string,
    documentKey: DocumentKey,
    artifact: DocxArtifactSource
  ): Promise<DocxOpenResult> {
    const owner = validJobId(jobId)
    const key = validDocumentKey(documentKey)
    const bytes = new Uint8Array(artifact.bytes)
    if (!/^[a-f0-9]{64}$/.test(artifact.sha256) || sha256(bytes) !== artifact.sha256) {
      throw new Error('Artifact DOCX hash mismatch')
    }

    await mkdir(this.#artifactRoot, { recursive: true, mode: 0o700 })
    const bindingId = docxBindingId(owner, key)
    const filename = canonicalArtifactFilename(artifact.filename)
    const candidates = [
      path.join(this.#artifactRoot, `${bindingId}-${filename}`),
      path.join(this.#artifactRoot, `${bindingId}-${artifact.sha256.slice(0, 12)}-${filename}`)
    ]

    return this.#serializeBinding(bindingId, async () => {
      const bound = await this.#bindings.getForJob(owner, key)
      if (bound) return this.#reloadBinding(bound.bindingId)
      for (const candidate of candidates) {
        try {
          const existing = await this.#files.read(candidate)
          if (existing.sha256 === artifact.sha256) return this.#bind(owner, key, candidate)
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
          try {
            await this.#files.writeNew(candidate, bytes)
            return this.#bind(owner, key, candidate)
          } catch (writeError) {
            if ((writeError as NodeJS.ErrnoException).code !== 'EEXIST') throw writeError
            const raced = await this.#files.read(candidate)
            if (raced.sha256 === artifact.sha256) return this.#bind(owner, key, candidate)
          }
        }
      }
      throw new Error('A different editable DOCX already occupies the artifact destination')
    })
  }

  async chooseFile(jobId: string, documentKey: DocumentKey): Promise<DocxOpenResult | null> {
    const owner = validJobId(jobId)
    const key = validDocumentKey(documentKey)
    return this.#serializeBinding(docxBindingId(owner, key), async () => {
      const selection = await this.#dialog.showOpenDialog({
        title: `Choose the canonical ${DOCX_DOCUMENT_LABELS[key]} DOCX`,
        properties: ['openFile'],
        filters: [{ name: 'Word document', extensions: ['docx'] }]
      })
      const selected = selection.filePaths[0]
      return selection.canceled || !selected ? null : this.#bind(owner, key, selected)
    })
  }

  async createBlank(jobId: string, documentKey: DocumentKey): Promise<DocxOpenResult | null> {
    const owner = validJobId(jobId)
    const key = validDocumentKey(documentKey)
    const label = DOCX_DOCUMENT_LABELS[key]
    const selection = await this.#dialog.showSaveDialog({
      title: `Create ${label} DOCX`,
      defaultPath: `${label.replaceAll(' ', '-')}.docx`,
      filters: [{ name: 'Word document', extensions: ['docx'] }]
    })
    if (selection.canceled || !selection.filePath) return null
    const bytes = await buildBlankDocx()
    const bindingId = docxBindingId(owner, key)
    return this.#serializeBinding(bindingId, async () => {
      try {
        const current = await this.#files.read(selection.filePath)
        await this.#files.replace(bindingId, selection.filePath, current.sha256, bytes, 'manual')
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
        await this.#files.writeNew(selection.filePath, bytes)
      }
      return this.#bind(owner, key, selection.filePath)
    })
  }

  async reload(bindingId: string): Promise<DocxOpenResult> {
    return this.#serializeBinding(bindingId, () => this.#reloadBinding(bindingId))
  }

  async #reloadBinding(bindingId: string): Promise<DocxOpenResult> {
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
    return this.#serializeBinding(request.bindingId, async () => {
      const binding = await this.#requireBinding(request.bindingId)
      const bytes = new Uint8Array(request.bytes)
      const expectedSavedSha256 = sha256(bytes)
      this.#watcher.expectSave(binding.bindingId, expectedSavedSha256)
      try {
        const saved = await this.#files.replace(binding.bindingId, binding.canonicalPath, request.expectedSha256, bytes)
        const updated = await this.#updatedBinding(binding, saved.file)
        await this.#bindings.put(updated)
        this.#watcher.update(updated)
        return { binding: updated, persistedGeneration: request.generation, recoveryId: saved.recovery.recoveryId }
      } finally {
        this.#watcher.clearExpectedSave(binding.bindingId, expectedSavedSha256)
      }
    })
  }

  async saveAs(bindingId: string, bytesBuffer: ArrayBuffer): Promise<DocxOpenResult | null> {
    return this.#serializeBinding(bindingId, async () => {
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
    })
  }

  async createRecovery(bindingId: string, reason: DocxRecoveryEntry['reason']): Promise<DocxRecoveryEntry> {
    return this.#serializeBinding(bindingId, async () => {
      const binding = await this.#requireBinding(bindingId)
      const file = await this.#files.read(binding.canonicalPath)
      return this.#files.createRecovery(binding.bindingId, binding.canonicalPath, file.bytes, reason)
    })
  }

  listRecoveries(bindingId: string): Promise<DocxRecoveryEntry[]> {
    return this.#files.listRecoveries(bindingId)
  }

  async restoreRecovery(bindingId: string, recoveryId: string): Promise<DocxOpenResult> {
    return this.#serializeBinding(bindingId, async () => {
      const binding = await this.#requireBinding(bindingId)
      const recovery = await this.#files.recoveryBytes(bindingId, recoveryId)
      const current = await this.#files.read(binding.canonicalPath)
      const expectedSavedSha256 = sha256(recovery)
      this.#watcher.expectSave(binding.bindingId, expectedSavedSha256)
      try {
        await this.#files.replace(bindingId, binding.canonicalPath, current.sha256, recovery, 'conflict')
        return await this.#reloadBinding(bindingId)
      } finally {
        this.#watcher.clearExpectedSave(binding.bindingId, expectedSavedSha256)
      }
    })
  }

  async inspect(jobId: string, documentKey: DocumentKey): Promise<{
    binding: DocxBinding
    context: DocumentContext
  }> {
    const worker = this.#worker
    if (!worker) throw new Error('DOCX worker unavailable')
    const binding = await this.#bindings.getForJob(validJobId(jobId), validDocumentKey(documentKey))
    if (!binding) throw new Error('DOCX is not bound on this device')
    return this.#serializeBinding(binding.bindingId, async () => {
      const currentBinding = await this.#requireBinding(binding.bindingId)
      const current = await this.#files.read(currentBinding.canonicalPath)
      const observedBinding = current.sha256 === currentBinding.sha256
        ? currentBinding
        : await this.#updatedBinding(currentBinding, current)
      if (observedBinding !== currentBinding) {
        await this.#bindings.put(observedBinding)
        this.#watcher.update(observedBinding)
      }
      const result = await worker.run({ kind: 'inspect', bytes: arrayBuffer(current.bytes) })
      if (result.kind !== 'inspect') throw new Error('Unexpected DOCX worker result')
      return { binding: observedBinding, context: result.context }
    })
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
    return this.#serializeBinding(binding.bindingId, async () => {
      const currentBinding = await this.#requireBinding(binding.bindingId)
      const current = await this.#files.read(currentBinding.canonicalPath)
      if (current.sha256 !== expectedSha256) throw new Error('DOCX changed outside JobOS')
      const result = await worker.run({ kind: 'apply', bytes: arrayBuffer(current.bytes), operations })
      if (result.kind !== 'apply') throw new Error('Unexpected DOCX worker result')
      const savedBytes = new Uint8Array(result.bytes)
      const expectedSavedSha256 = sha256(savedBytes)
      this.#watcher.expectSave(currentBinding.bindingId, expectedSavedSha256)
      try {
        const saved = await this.#files.replace(
          currentBinding.bindingId,
          currentBinding.canonicalPath,
          expectedSha256,
          savedBytes,
          'agent'
        )
        const updated = await this.#updatedBinding(currentBinding, saved.file)
        await this.#bindings.put(updated)
        this.#watcher.update(updated)
        this.#watcher.notifyChanged(updated)
        return { binding: updated, context: result.context, recoveryId: saved.recovery.recoveryId }
      } finally {
        this.#watcher.clearExpectedSave(currentBinding.bindingId, expectedSavedSha256)
      }
    })
  }

  async unbind(bindingId: string): Promise<void> {
    await this.#serializeBinding(bindingId, async () => {
      this.#watcher.unwatch(bindingId)
      await this.#bindings.remove(bindingId)
    })
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

  async #serializeBinding<T>(bindingId: string, operation: () => Promise<T>): Promise<T> {
    const previous = this.#bindingQueues.get(bindingId) ?? Promise.resolve()
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    const tail = previous.catch(() => undefined).then(() => gate)
    this.#bindingQueues.set(bindingId, tail)
    await previous.catch(() => undefined)
    try {
      return await operation()
    } finally {
      release()
      if (this.#bindingQueues.get(bindingId) === tail) this.#bindingQueues.delete(bindingId)
    }
  }
}
