// @vitest-environment node

import { mkdtemp, readFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { DocxBinding } from '../shared/docxDocuments.js'
import { docxBindingId, LocalDocxBindingStore } from './localDocxBindingStore.js'

let root: string
let filePath: string
let store: LocalDocxBindingStore

function binding(revision = 1): DocxBinding {
  return {
    schemaVersion: 1,
    bindingId: docxBindingId('(FAKE)-job-7', 'resume'),
    jobId: '(FAKE)-job-7',
    documentKey: 'resume',
    documentLabel: 'Resume',
    canonicalPath: '/tmp/(FAKE)-resume.docx',
    filename: '(FAKE)-resume.docx',
    sha256: '0'.repeat(64),
    byteLength: 123,
    modifiedAtMs: 1,
    revision,
    capabilities: { mode: 'editable', protectedBlockCount: 0, editableBlockCount: 1, reasons: [] },
    createdAt: '2026-08-08T00:00:00Z',
    updatedAt: '2026-08-08T00:00:00Z'
  }
}

beforeEach(async () => {
  root = await mkdtemp(path.join(os.tmpdir(), 'jobos-docx-bindings-'))
  filePath = path.join(root, 'bindings.json')
  store = new LocalDocxBindingStore(filePath)
})

afterEach(async () => { await rm(root, { recursive: true, force: true }) })

describe('LocalDocxBindingStore', () => {
  it('atomically stores and replaces one local job/document binding', async () => {
    await store.put(binding())
    await store.put(binding(2))
    expect((await store.list('(FAKE)-job-7')).map(item => item.revision)).toEqual([2])
    expect(JSON.parse(await readFile(filePath, 'utf8')).schemaVersion).toBe(1)
  })

  it('removes a binding without affecting the canonical file', async () => {
    await store.put(binding())
    await store.remove(binding().bindingId)
    expect(await store.list()).toEqual([])
  })
})
