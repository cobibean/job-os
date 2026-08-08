import { watch, type FSWatcher } from 'node:fs'
import path from 'node:path'

import type { DocxBinding, DocxExternalChangeEvent } from '../shared/docxDocuments.js'
import { DocxFileStore } from './docxFileStore.js'

interface WatchedBinding {
  binding: DocxBinding
  watcher: FSWatcher
  timer: NodeJS.Timeout | null
}

export class DocxFileWatcher {
  readonly #fileStore: DocxFileStore
  readonly #emit: (event: DocxExternalChangeEvent) => void
  readonly #watched = new Map<string, WatchedBinding>()

  constructor(fileStore: DocxFileStore, emit: (event: DocxExternalChangeEvent) => void) {
    this.#fileStore = fileStore
    this.#emit = emit
  }

  watch(binding: DocxBinding): void {
    this.unwatch(binding.bindingId)
    const filename = path.basename(binding.canonicalPath)
    const watched: WatchedBinding = {
      binding,
      timer: null,
      watcher: watch(path.dirname(binding.canonicalPath), (_event, changed) => {
        if (changed && changed.toString() !== filename) return
        if (watched.timer) clearTimeout(watched.timer)
        watched.timer = setTimeout(() => { void this.#inspect(watched) }, 180)
      })
    }
    watched.watcher.on('error', () => undefined)
    this.#watched.set(binding.bindingId, watched)
  }

  update(binding: DocxBinding): void {
    const current = this.#watched.get(binding.bindingId)
    if (current && current.binding.canonicalPath === binding.canonicalPath) current.binding = binding
    else this.watch(binding)
  }

  unwatch(bindingId: string): void {
    const current = this.#watched.get(bindingId)
    if (!current) return
    if (current.timer) clearTimeout(current.timer)
    current.watcher.close()
    this.#watched.delete(bindingId)
  }

  dispose(): void {
    for (const bindingId of this.#watched.keys()) this.unwatch(bindingId)
  }

  async #inspect(watched: WatchedBinding): Promise<void> {
    try {
      const file = await this.#fileStore.read(watched.binding.canonicalPath)
      if (file.sha256 === watched.binding.sha256) return
      this.#emit({
        bindingId: watched.binding.bindingId,
        jobId: watched.binding.jobId,
        documentKey: watched.binding.documentKey,
        kind: 'changed',
        sha256: file.sha256,
        modifiedAtMs: file.modifiedAtMs
      })
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') return
      this.#emit({
        bindingId: watched.binding.bindingId,
        jobId: watched.binding.jobId,
        documentKey: watched.binding.documentKey,
        kind: 'missing'
      })
    }
  }
}
