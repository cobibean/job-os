// @vitest-environment node

import { afterEach, describe, expect, test, vi } from 'vitest'

import type { DocxBinding } from '../shared/docxDocuments.js'
import { DocxFileWatcher } from './docxFileWatcher.js'
import type { DocxFileStore } from './docxFileStore.js'

const binding: DocxBinding = {
  schemaVersion: 1,
  bindingId: 'docx_fake_resume',
  jobId: 'fake-job',
  documentKey: 'resume',
  documentLabel: 'Resume',
  canonicalPath: '/protected/(FAKE)-resume.docx',
  filename: '(FAKE)-resume.docx',
  sha256: 'a'.repeat(64),
  byteLength: 100,
  modifiedAtMs: 1_000,
  revision: 1,
  capabilities: {
    mode: 'editable',
    protectedBlockCount: 0,
    editableBlockCount: 1,
    reasons: []
  },
  createdAt: '2026-08-08T00:00:00.000Z',
  updatedAt: '2026-08-08T00:00:00.000Z'
}

const watchers: DocxFileWatcher[] = []

afterEach(() => {
  for (const watcher of watchers) watcher.dispose()
  watchers.length = 0
  vi.restoreAllMocks()
})

function createWatcher(options: ConstructorParameters<typeof DocxFileWatcher>[2]) {
  const read = vi.fn()
  const emit = vi.fn()
  const watcher = new DocxFileWatcher(
    { read } as unknown as DocxFileStore,
    emit,
    options
  )
  watchers.push(watcher)
  return { emit, read, watcher }
}

describe('DocxFileWatcher', () => {
  test('registering a binding never touches the protected path synchronously', () => {
    const statFile = vi.fn()
    const { watcher } = createWatcher({ pollIntervalMs: 10, statFile })

    watcher.watch(binding)

    expect(statFile).not.toHaveBeenCalled()
  })

  test('suppresses the exact hash reserved for an in-progress JobOS save', async () => {
    const statFile = vi.fn().mockResolvedValue({ size: 120, mtimeMs: 2_000 })
    const { emit, read, watcher } = createWatcher({ pollIntervalMs: 1, statFile })
    read.mockResolvedValue({
      bytes: new Uint8Array(120),
      sha256: 'b'.repeat(64),
      modifiedAtMs: 2_000
    })
    watcher.expectSave(binding.bindingId, 'b'.repeat(64))

    watcher.watch(binding)

    await vi.waitFor(() => expect(read).toHaveBeenCalled())
    await new Promise(resolve => setTimeout(resolve, 10))
    expect(emit).not.toHaveBeenCalled()
  })

  test('publishes an explicit local mutation after an agent write', () => {
    const { emit, watcher } = createWatcher({})
    const updated = { ...binding, sha256: 'c'.repeat(64), modifiedAtMs: 3_000, revision: 2 }

    watcher.notifyChanged(updated)

    expect(emit).toHaveBeenCalledWith({
      bindingId: updated.bindingId,
      jobId: updated.jobId,
      documentKey: updated.documentKey,
      kind: 'changed',
      sha256: updated.sha256,
      modifiedAtMs: updated.modifiedAtMs
    })
  })

  test('polls asynchronously and emits one external-change event for new bytes', async () => {
    const statFile = vi.fn().mockResolvedValue({ size: 120, mtimeMs: 2_000 })
    const { emit, read, watcher } = createWatcher({ pollIntervalMs: 1, statFile })
    read.mockResolvedValue({
      bytes: new Uint8Array(120),
      sha256: 'b'.repeat(64),
      modifiedAtMs: 2_000
    })

    watcher.watch(binding)

    await vi.waitFor(() => {
      expect(emit).toHaveBeenCalledWith({
        bindingId: binding.bindingId,
        jobId: binding.jobId,
        documentKey: binding.documentKey,
        kind: 'changed',
        sha256: 'b'.repeat(64),
        modifiedAtMs: 2_000
      })
    })
    await new Promise(resolve => setTimeout(resolve, 10))
    expect(emit).toHaveBeenCalledTimes(1)
  })
})
