// @vitest-environment node

import { copyFile, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path, { resolve } from 'node:path'

import { buildBlankDocx } from '@jobos/docx-engine'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DocxDocumentsService } from './docxDocuments.js'
import { DocxFileStore, sha256 } from './docxFileStore.js'
import { LocalDocxBindingStore } from './localDocxBindingStore.js'

let root: string
let selected: string
let service: DocxDocumentsService

beforeEach(async () => {
  root = await mkdtemp(path.join(os.tmpdir(), 'jobos-docx-service-'))
  selected = path.join(root, '(FAKE)-resume.docx')
  await copyFile(resolve(process.cwd(), '../../packages/docx-engine/tests/fixtures/(FAKE)-polished-resume.docx'), selected)
  service = new DocxDocumentsService({
    dialog: {
      showOpenDialog: vi.fn(async () => ({ canceled: false, filePaths: [selected] })),
      showSaveDialog: vi.fn(async () => ({ canceled: true, filePath: '' }))
    },
    bindings: new LocalDocxBindingStore(path.join(root, 'bindings.json')),
    files: new DocxFileStore({ recoveryRoot: path.join(root, 'recovery') }),
    emit: vi.fn(),
    worker: {
      run: vi.fn(async () => ({
        kind: 'inspect',
        context: { revision: '(FAKE)-context-1', blocks: [] },
        capabilities: {
          mode: 'editable',
          protectedBlockCount: 0,
          editableBlockCount: 1,
          reasons: []
        }
      }))
    } as never
  })
  await service.initialize()
})

afterEach(async () => { service.dispose(); await rm(root, { recursive: true, force: true }) })

describe('DocxDocumentsService', () => {
  it('binds the selected file itself and reopens the same canonical bytes', async () => {
    const opened = await service.chooseFile('(FAKE)-job-7', 'resume')
    expect(opened?.binding.canonicalPath).toBe(selected)
    expect(opened?.binding.filename).toBe('(FAKE)-resume.docx')
    expect(opened?.binding.capabilities.editableBlockCount).toBeGreaterThan(0)
    const reopened = await service.openBound('(FAKE)-job-7', 'resume')
    expect(reopened?.binding.sha256).toBe(opened?.binding.sha256)
    expect(new Uint8Array(reopened!.bytes)).toEqual(new Uint8Array(await readFile(selected)))
  })

  it('refreshes portable inspection metadata after an external file replacement', async () => {
    const opened = await service.chooseFile('(FAKE)-job-7', 'resume')
    const external = await buildBlankDocx()
    await writeFile(selected, external)

    const inspected = await service.inspect('(FAKE)-job-7', 'resume')

    expect(inspected.binding.sha256).toBe(sha256(external))
    expect(inspected.binding.sha256).not.toBe(opened?.binding.sha256)
    expect(inspected.binding.revision).toBe((opened?.binding.revision ?? 0) + 1)
  })

  it('keeps unbinding separate from deleting the user file', async () => {
    const opened = await service.chooseFile('(FAKE)-job-7', 'resume')
    await service.unbind(opened!.binding.bindingId)
    expect(await service.openBound('(FAKE)-job-7', 'resume')).toBeNull()
    expect((await readFile(selected)).byteLength).toBeGreaterThan(100)
  })
})
