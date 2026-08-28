import { stat } from 'node:fs/promises'

import type { DocxBinding, DocxExternalChangeEvent } from '../../../shared/docxDocuments.js'
import { DocxFileStore } from './docxFileStore.js'

interface WatchedBinding {
  binding: DocxBinding
  missing: boolean
  timer: NodeJS.Timeout | null
}

interface DocxFileWatcherOptions {
  pollIntervalMs?: number
  statFile?: (filePath: string) => Promise<{ size: number; mtimeMs: number }>
}

const DEFAULT_POLL_INTERVAL_MS = 1_000

export class DocxFileWatcher {
  readonly #fileStore: DocxFileStore
  readonly #emit: (event: DocxExternalChangeEvent) => void
  readonly #pollIntervalMs: number
  readonly #statFile: NonNullable<DocxFileWatcherOptions['statFile']>
  readonly #expectedSaveHashes = new Map<string, Set<string>>()
  readonly #watched = new Map<string, WatchedBinding>()

  constructor(
    fileStore: DocxFileStore,
    emit: (event: DocxExternalChangeEvent) => void,
    options: DocxFileWatcherOptions = {}
  ) {
    this.#fileStore = fileStore
    this.#emit = emit
    this.#pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS
    this.#statFile = options.statFile ?? stat
  }

  watch(binding: DocxBinding): void {
    this.unwatch(binding.bindingId)
    const watched: WatchedBinding = {
      binding,
      missing: false,
      timer: null
    }
    this.#watched.set(binding.bindingId, watched)
    this.#schedule(watched)
  }

  update(binding: DocxBinding): void {
    const current = this.#watched.get(binding.bindingId)
    if (current && current.binding.canonicalPath === binding.canonicalPath) {
      current.binding = binding
      current.missing = false
    } else {
      this.watch(binding)
    }
  }

  expectSave(bindingId: string, sha256: string): void {
    const hashes = this.#expectedSaveHashes.get(bindingId) ?? new Set<string>()
    hashes.add(sha256)
    this.#expectedSaveHashes.set(bindingId, hashes)
  }

  clearExpectedSave(bindingId: string, sha256: string): void {
    const hashes = this.#expectedSaveHashes.get(bindingId)
    if (!hashes) return
    hashes.delete(sha256)
    if (hashes.size === 0) this.#expectedSaveHashes.delete(bindingId)
  }

  notifyChanged(binding: DocxBinding): void {
    this.#emit({
      bindingId: binding.bindingId,
      jobId: binding.jobId,
      documentKey: binding.documentKey,
      kind: 'changed',
      sha256: binding.sha256,
      modifiedAtMs: binding.modifiedAtMs
    })
  }

  unwatch(bindingId: string): void {
    const current = this.#watched.get(bindingId)
    if (!current) return
    if (current.timer) clearTimeout(current.timer)
    this.#watched.delete(bindingId)
  }

  dispose(): void {
    for (const bindingId of this.#watched.keys()) this.unwatch(bindingId)
    this.#expectedSaveHashes.clear()
  }

  #schedule(watched: WatchedBinding): void {
    if (this.#watched.get(watched.binding.bindingId) !== watched) return
    watched.timer = setTimeout(() => {
      watched.timer = null
      void this.#inspect(watched)
    }, this.#pollIntervalMs)
    watched.timer.unref()
  }

  async #inspect(watched: WatchedBinding): Promise<void> {
    try {
      const metadata = await this.#statFile(watched.binding.canonicalPath)
      watched.missing = false
      if (
        metadata.size === watched.binding.byteLength
        && metadata.mtimeMs === watched.binding.modifiedAtMs
      ) return

      const file = await this.#fileStore.read(watched.binding.canonicalPath)
      watched.binding = {
        ...watched.binding,
        byteLength: file.bytes.byteLength,
        modifiedAtMs: file.modifiedAtMs
      }
      const expectedSaveHashes = this.#expectedSaveHashes.get(watched.binding.bindingId)
      if (file.sha256 === watched.binding.sha256 || expectedSaveHashes?.has(file.sha256)) return

      this.#emit({
        bindingId: watched.binding.bindingId,
        jobId: watched.binding.jobId,
        documentKey: watched.binding.documentKey,
        kind: 'changed',
        sha256: file.sha256,
        modifiedAtMs: file.modifiedAtMs
      })
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT' && !watched.missing) {
        watched.missing = true
        this.#emit({
          bindingId: watched.binding.bindingId,
          jobId: watched.binding.jobId,
          documentKey: watched.binding.documentKey,
          kind: 'missing'
        })
      }
    } finally {
      this.#schedule(watched)
    }
  }
}
