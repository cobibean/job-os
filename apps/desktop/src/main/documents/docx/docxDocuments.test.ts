// @vitest-environment node

import { copyFile, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path, { resolve } from 'node:path'

import { buildBlankDocx } from '@jobos/docx-engine'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import type { DocxExternalChangeEvent } from '../../../shared/docxDocuments.js'
import type { DocxWorkerRequest } from '../../../shared/docxWorker.js'
import { DocxDocumentsService } from './docxDocuments.js'
import { DocxFileStore, sha256 } from './docxFileStore.js'
import { LocalDocxBindingStore } from './localDocxBindingStore.js'

let root: string
let selected: string
let service: DocxDocumentsService
let emit: Mock<(event: DocxExternalChangeEvent) => void>
let workerRun: ReturnType<typeof vi.fn>

beforeEach(async () => {
  root = await mkdtemp(path.join(os.tmpdir(), 'jobos-docx-service-'))
  selected = path.join(root, '(FAKE)-resume.docx')
  await copyFile(resolve(process.cwd(), '../../packages/docx-engine/tests/fixtures/(FAKE)-polished-resume.docx'), selected)
  emit = vi.fn()
  workerRun = vi.fn(async (request: DocxWorkerRequest) => {
    const context = { revision: '(FAKE)-context-1', blocks: [] }
    const capabilities = {
      mode: 'editable' as const,
      protectedBlockCount: 0,
      editableBlockCount: 1,
      reasons: []
    }
    return request.kind === 'apply'
      ? { kind: 'apply' as const, bytes: request.bytes, context, capabilities }
      : { kind: 'inspect' as const, context, capabilities }
  })
  service = new DocxDocumentsService({
    dialog: {
      showOpenDialog: vi.fn(async () => ({ canceled: false, filePaths: [selected] })),
      showSaveDialog: vi.fn(async () => ({ canceled: true, filePath: '' }))
    },
    bindings: new LocalDocxBindingStore(path.join(root, 'bindings.json')),
    files: new DocxFileStore({ recoveryRoot: path.join(root, 'recovery') }),
    artifactRoot: path.join(root, 'artifacts'),
    emit,
    worker: {
      run: workerRun
    } as never
  })
  await service.initialize()
})

afterEach(async () => { service.dispose(); await rm(root, { recursive: true, force: true }) })

describe.skipIf(process.platform !== 'darwin')('DocxDocumentsService', () => {
  it('binds the selected file itself and reopens the same canonical bytes', async () => {
    const opened = await service.chooseFile('(FAKE)-job-7', 'resume')
    expect(opened?.binding.canonicalPath).toBe(selected)
    expect(opened?.binding.filename).toBe('(FAKE)-resume.docx')
    expect(opened?.binding.capabilities.editableBlockCount).toBeGreaterThan(0)
    const reopened = await service.openBound('(FAKE)-job-7', 'resume')
    expect(reopened?.binding.sha256).toBe(opened?.binding.sha256)
    expect(new Uint8Array(reopened!.bytes)).toEqual(new Uint8Array(await readFile(selected)))
  })

  it('materializes a registered packet DOCX once and reopens that stable editable binding', async () => {
    const source = new Uint8Array(await readFile(selected))
    const opened = await service.openArtifact('(FAKE)-northstar-job', 'resume', {
      filename: '(FAKE)-Northstar-AI-Labs-Resume.docx',
      sha256: sha256(source),
      bytes: source.buffer.slice(source.byteOffset, source.byteOffset + source.byteLength) as ArrayBuffer
    })

    expect(opened.binding.canonicalPath).toContain(path.join(root, 'artifacts'))
    expect(opened.binding.filename).toContain('(FAKE)-Northstar-AI-Labs-Resume.docx')
    expect(new Uint8Array(opened.bytes)).toEqual(source)

    const reopened = await service.openBound('(FAKE)-northstar-job', 'resume')
    expect(reopened?.binding.canonicalPath).toBe(opened.binding.canonicalPath)
    expect(new Uint8Array(reopened!.bytes)).toEqual(source)
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

  it('serializes autosave and Save a Copy so the new binding cannot revert to the old path', async () => {
    const copy = path.join(root, '(FAKE)-resume-copy.docx')
    const raceBindings = new LocalDocxBindingStore(path.join(root, 'race-bindings.json'))
    let releaseReplace!: () => void
    let enteredReplace!: () => void
    const replaceEntered = new Promise<void>(resolve => { enteredReplace = resolve })
    const replaceGate = new Promise<void>(resolve => { releaseReplace = resolve })
    const showSaveDialog = vi.fn(async () => ({ canceled: false, filePath: copy }))
    const guardedService = new DocxDocumentsService({
      dialog: {
        showOpenDialog: vi.fn(async () => ({ canceled: false, filePaths: [selected] })),
        showSaveDialog
      },
      bindings: raceBindings,
      files: new DocxFileStore({
        recoveryRoot: path.join(root, 'race-recovery'),
        beforeAtomicReplace: async () => {
          enteredReplace()
          await replaceGate
        }
      }),
      artifactRoot: path.join(root, 'race-artifacts'),
      emit: vi.fn()
    })
    await guardedService.initialize()

    try {
      const opened = await guardedService.chooseFile('(FAKE)-race-job', 'resume')
      const replacement = await buildBlankDocx()
      const save = guardedService.save({
        bindingId: opened!.binding.bindingId,
        bytes: replacement.buffer.slice(
          replacement.byteOffset,
          replacement.byteOffset + replacement.byteLength
        ) as ArrayBuffer,
        expectedSha256: opened!.binding.sha256,
        generation: 1
      })
      await replaceEntered

      const saveAs = guardedService.saveAs(opened!.binding.bindingId, opened!.bytes)
      expect(showSaveDialog).not.toHaveBeenCalled()

      releaseReplace()
      await save
      const copied = await saveAs
      const registered = await raceBindings.get(opened!.binding.bindingId)

      expect(showSaveDialog).toHaveBeenCalledTimes(1)
      expect(copied?.binding.canonicalPath).toBe(copy)
      expect(registered?.canonicalPath).toBe(copy)
    } finally {
      guardedService.dispose()
    }
  })

  it('serializes choosing a new canonical file behind an in-flight agent edit', async () => {
    const reboundPath = path.join(root, '(FAKE)-chosen-after-agent-edit.docx')
    await copyFile(selected, reboundPath)
    const raceBindings = new LocalDocxBindingStore(path.join(root, 'rebind-race-bindings.json'))
    let chosenPath = selected
    let releaseWorker!: () => void
    let workerEntered!: () => void
    const workerGate = new Promise<void>(resolve => { releaseWorker = resolve })
    const enteredWorker = new Promise<void>(resolve => { workerEntered = resolve })
    const showOpenDialog = vi.fn(async () => ({ canceled: false, filePaths: [chosenPath] }))
    const guardedService = new DocxDocumentsService({
      dialog: {
        showOpenDialog,
        showSaveDialog: vi.fn(async () => ({ canceled: true, filePath: '' }))
      },
      bindings: raceBindings,
      files: new DocxFileStore({ recoveryRoot: path.join(root, 'rebind-race-recovery') }),
      artifactRoot: path.join(root, 'rebind-race-artifacts'),
      emit: vi.fn(),
      worker: {
        run: vi.fn(async (request: DocxWorkerRequest) => {
          const context = { revision: '(FAKE)-context-race', blocks: [] }
          const capabilities = {
            mode: 'editable' as const,
            protectedBlockCount: 0,
            editableBlockCount: 1,
            reasons: []
          }
          if (request.kind === 'apply') {
            workerEntered()
            await workerGate
            return { kind: 'apply' as const, bytes: request.bytes, context, capabilities }
          }
          return { kind: 'inspect' as const, context, capabilities }
        })
      } as never
    })
    await guardedService.initialize()

    try {
      const opened = await guardedService.chooseFile('(FAKE)-rebind-race-job', 'resume')
      const apply = guardedService.applyOperations(
        '(FAKE)-rebind-race-job',
        'resume',
        opened!.binding.sha256,
        []
      )
      await enteredWorker

      chosenPath = reboundPath
      const choose = guardedService.chooseFile('(FAKE)-rebind-race-job', 'resume')
      expect(showOpenDialog).toHaveBeenCalledTimes(1)
      expect((await raceBindings.get(opened!.binding.bindingId))?.canonicalPath).toBe(selected)

      releaseWorker()
      await apply
      const rebound = await choose
      const registered = await raceBindings.get(opened!.binding.bindingId)

      expect(showOpenDialog).toHaveBeenCalledTimes(2)
      expect(rebound?.binding.canonicalPath).toBe(reboundPath)
      expect(registered?.canonicalPath).toBe(reboundPath)
    } finally {
      guardedService.dispose()
    }
  })

  it('notifies open renderer views after an agent operation updates the canonical DOCX', async () => {
    const opened = await service.chooseFile('(FAKE)-job-7', 'resume')
    expect(opened).not.toBeNull()

    const result = await service.applyOperations('(FAKE)-job-7', 'resume', opened!.binding.sha256, [])

    expect(workerRun).toHaveBeenLastCalledWith({
      kind: 'apply',
      bytes: expect.any(ArrayBuffer),
      operations: []
    })
    expect(emit).toHaveBeenCalledWith({
      bindingId: result.binding.bindingId,
      jobId: result.binding.jobId,
      documentKey: result.binding.documentKey,
      kind: 'changed',
      sha256: result.binding.sha256,
      modifiedAtMs: result.binding.modifiedAtMs
    })
  })
})
