import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { JobOsRendererBridge } from '../../shared/contracts'
import type { DocxBinding, DocxExternalChangeEvent, DocxOpenResult, DocxRecoveryEntry } from '../../shared/docxDocuments'
import { DocxDocumentEditorShell } from './DocxDocumentEditorShell'

function binding(bytes: Uint8Array): DocxBinding {
  return {
    schemaVersion: 1,
    bindingId: 'docx_000000000000000000000000',
    jobId: '(FAKE)-job-7',
    documentKey: 'resume',
    documentLabel: 'Resume',
    canonicalPath: '/tmp/(FAKE)-polished-resume.docx',
    filename: '(FAKE)-polished-resume.docx',
    sha256: 'a'.repeat(64),
    byteLength: bytes.byteLength,
    modifiedAtMs: 1,
    revision: 1,
    capabilities: { mode: 'editable', protectedBlockCount: 0, editableBlockCount: 12, reasons: [] },
    createdAt: '2026-08-08T00:00:00Z',
    updatedAt: '2026-08-08T00:00:00Z'
  }
}

async function fixture(): Promise<DocxOpenResult> {
  const bytes = new Uint8Array(await readFile(resolve(process.cwd(), '../../packages/docx-engine/tests/fixtures/(FAKE)-polished-resume.docx')))
  return { binding: binding(bytes), bytes: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer }
}

function installBridge(opened: DocxOpenResult) {
  let listener: ((event: DocxExternalChangeEvent) => void) | null = null
  const bridge = {
    subscribe: vi.fn((next: (event: DocxExternalChangeEvent) => void) => { listener = next; return () => { listener = null } }),
    listRecoveries: vi.fn(async (): Promise<DocxRecoveryEntry[]> => []),
    reload: vi.fn(async () => opened),
    save: vi.fn(async request => ({
      binding: opened.binding,
      persistedGeneration: request.generation,
      recoveryId: 'recovery_fake_alignment'
    })),
    saveAs: vi.fn(),
    createRecovery: vi.fn(),
    restoreRecovery: vi.fn()
  }
  Object.defineProperty(window, 'jobos', { configurable: true, value: { docxDocuments: bridge } as unknown as JobOsRendererBridge })
  return { bridge, emit: (event: DocxExternalChangeEvent) => listener?.(event) }
}

afterEach(() => {
  cleanup()
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  vi.restoreAllMocks()
})

describe('OOXML-retaining DOCX editor', () => {
  function insertEditorText(text: string): void {
    const editor = document.querySelector('.ProseMirror') as HTMLElement
    const paragraph = editor.querySelector('p')
    if (!paragraph) throw new Error('Editable paragraph unavailable')
    paragraph.textContent = `${text}${paragraph.textContent ?? ''}`
    fireEvent.input(editor, { data: text, inputType: 'insertText' })
  }

  it('renders the bound canonical DOCX with JobOS controls and source identity', async () => {
    const opened = await fixture()
    installBridge(opened)
    render(<DocxDocumentEditorShell jobLabel="(FAKE) Northstar · Product Lead" onExit={vi.fn()} onPrepareClose={vi.fn()} opened={opened} />)

    expect(await screen.findByRole('button', { name: /Back to Review/ })).not.toBeNull()
    expect(screen.getByRole('toolbar', { name: 'DOCX formatting' })).not.toBeNull()
    expect(screen.getByText('(FAKE)-polished-resume.docx')).not.toBeNull()
    expect(screen.getByText('/tmp/(FAKE)-polished-resume.docx')).not.toBeNull()
    expect(screen.getByRole('button', { name: /Checkpoint/ })).not.toBeNull()
    expect(screen.getByRole('button', { name: /Save a Copy/ })).not.toBeNull()
    await waitFor(() => expect(document.querySelector('.ProseMirror')).not.toBeNull())
    expect(document.querySelector('.jobos-docx-canvas')).not.toBeNull()
  })

  it('exposes paragraph alignment controls and applies center alignment to the active paragraph', async () => {
    const opened = await fixture()
    installBridge(opened)
    render(<DocxDocumentEditorShell jobLabel="(FAKE) Northstar · Product Lead" onExit={vi.fn()} onPrepareClose={vi.fn()} opened={opened} />)

    await screen.findByRole('toolbar', { name: 'DOCX formatting' })
    expect(screen.getByRole('button', { name: 'Align left' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Align center' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Align right' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Justify' })).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Align center' }))

    await waitFor(() => {
      const paragraph = document.querySelector('.ProseMirror p') as HTMLElement | null
      expect(paragraph?.style.textAlign).toBe('center')
      expect(screen.getByRole('button', { name: 'Align center' }).getAttribute('aria-pressed')).toBe('true')
    })
  })

  it('locks editing while Save a Copy is awaiting its destination', async () => {
    const opened = await fixture()
    const { bridge } = installBridge(opened)
    let prepareClose: (() => Promise<boolean>) | undefined
    const onPrepareClose = vi.fn((handler: () => Promise<boolean>) => { prepareClose = handler })
    let finishSaveAs!: (value: DocxOpenResult | null) => void
    bridge.saveAs.mockImplementation(() => new Promise(resolve => { finishSaveAs = resolve }))
    render(<DocxDocumentEditorShell jobLabel="(FAKE) Northstar · Product Lead" onExit={vi.fn()} onPrepareClose={onPrepareClose} opened={opened} />)
    await screen.findByRole('toolbar', { name: 'DOCX formatting' })

    fireEvent.click(screen.getByRole('button', { name: 'Save a Copy…' }))

    await waitFor(() => expect(bridge.saveAs).toHaveBeenCalledTimes(1))
    expect(document.querySelector('.ProseMirror')?.getAttribute('contenteditable')).toBe('false')
    expect((screen.getByRole('button', { name: 'Align center' }) as HTMLButtonElement).disabled).toBe(true)
    expect(await prepareClose?.()).toBe(false)

    await act(async () => { finishSaveAs(null); await Promise.resolve() })
    await waitFor(() => expect(document.querySelector('.ProseMirror')?.getAttribute('contenteditable')).toBe('true'))
    expect(await prepareClose?.()).toBe(true)
  })

  it('closes the initial subscription gap before presenting editable bytes', async () => {
    const opened = await fixture()
    const newer: DocxOpenResult = {
      ...opened,
      binding: {
        ...opened.binding,
        filename: '(FAKE)-newer-initial.docx',
        canonicalPath: '/tmp/(FAKE)-newer-initial.docx',
        sha256: 'b'.repeat(64),
        revision: 2,
        modifiedAtMs: 2
      }
    }
    let resolveInitial!: (value: DocxOpenResult) => void
    const { bridge, emit } = installBridge(opened)
    bridge.reload
      .mockImplementationOnce(() => new Promise(resolve => { resolveInitial = resolve }))
      .mockResolvedValue(newer)
    render(<DocxDocumentEditorShell jobLabel="(FAKE) Northstar · Product Lead" onExit={vi.fn()} onPrepareClose={vi.fn()} opened={opened} />)
    await waitFor(() => expect(bridge.reload).toHaveBeenCalledTimes(1))

    emit({
      bindingId: newer.binding.bindingId,
      jobId: newer.binding.jobId,
      documentKey: newer.binding.documentKey,
      kind: 'changed',
      sha256: newer.binding.sha256,
      modifiedAtMs: newer.binding.modifiedAtMs
    })
    await act(async () => { resolveInitial(opened); await Promise.resolve() })

    expect(await screen.findByText('(FAKE)-newer-initial.docx')).not.toBeNull()
    expect(screen.queryByText('(FAKE)-polished-resume.docx')).toBeNull()
    expect(bridge.reload).toHaveBeenCalledTimes(2)
  })

  it('keeps the newest external source when reloads resolve in reverse order', async () => {
    const opened = await fixture()
    const { bridge, emit } = installBridge(opened)
    render(<DocxDocumentEditorShell jobLabel="(FAKE) Northstar · Product Lead" onExit={vi.fn()} onPrepareClose={vi.fn()} opened={opened} />)
    await screen.findByRole('toolbar', { name: 'DOCX formatting' })

    const older: DocxOpenResult = {
      ...opened,
      binding: { ...opened.binding, filename: '(FAKE)-older.docx', sha256: 'c'.repeat(64), revision: 2, modifiedAtMs: 2 }
    }
    const newer: DocxOpenResult = {
      ...opened,
      binding: { ...opened.binding, filename: '(FAKE)-newest.docx', sha256: 'd'.repeat(64), revision: 3, modifiedAtMs: 3 }
    }
    let resolveOlder!: (value: DocxOpenResult) => void
    bridge.reload
      .mockImplementationOnce(() => new Promise(resolve => { resolveOlder = resolve }))
      .mockResolvedValue(newer)

    emit({
      bindingId: older.binding.bindingId,
      jobId: older.binding.jobId,
      documentKey: older.binding.documentKey,
      kind: 'changed',
      sha256: older.binding.sha256,
      modifiedAtMs: older.binding.modifiedAtMs
    })
    await waitFor(() => expect(bridge.reload).toHaveBeenCalledTimes(2))
    emit({
      bindingId: newer.binding.bindingId,
      jobId: newer.binding.jobId,
      documentKey: newer.binding.documentKey,
      kind: 'changed',
      sha256: newer.binding.sha256,
      modifiedAtMs: newer.binding.modifiedAtMs
    })

    expect(await screen.findByText('(FAKE)-newest.docx')).not.toBeNull()
    await act(async () => { resolveOlder(older); await Promise.resolve() })
    await waitFor(() => {
      expect(screen.queryByText('(FAKE)-older.docx')).toBeNull()
      expect(screen.getByText('(FAKE)-newest.docx')).not.toBeNull()
    })
  })

  it('does not let a delayed checkpoint restore replace a newer agent mutation', async () => {
    const opened = await fixture()
    const recovery = {
      recoveryId: 'recovery_fake_restore_race',
      bindingId: opened.binding.bindingId,
      filename: opened.binding.filename,
      sha256: opened.binding.sha256,
      byteLength: opened.binding.byteLength,
      reason: 'manual' as const,
      createdAt: '2026-08-08T01:00:00Z'
    }
    const restored: DocxOpenResult = {
      ...opened,
      binding: { ...opened.binding, filename: '(FAKE)-restored-older.docx', sha256: 'c'.repeat(64), revision: 2 }
    }
    const newer: DocxOpenResult = {
      ...opened,
      binding: { ...opened.binding, filename: '(FAKE)-agent-newest.docx', sha256: 'd'.repeat(64), revision: 3, modifiedAtMs: 3 }
    }
    let resolveRestore!: (value: DocxOpenResult) => void
    const { bridge, emit } = installBridge(opened)
    bridge.listRecoveries.mockResolvedValue([recovery])
    bridge.restoreRecovery.mockImplementation(() => new Promise(resolve => { resolveRestore = resolve }))
    render(<DocxDocumentEditorShell jobLabel="(FAKE) Northstar · Product Lead" onExit={vi.fn()} onPrepareClose={vi.fn()} opened={opened} />)

    const restoreButton = await screen.findByRole('button', { name: /manual/i })
    fireEvent.click(restoreButton)
    await waitFor(() => expect(bridge.restoreRecovery).toHaveBeenCalledTimes(1))

    bridge.reload.mockResolvedValue(newer)
    emit({
      bindingId: newer.binding.bindingId,
      jobId: newer.binding.jobId,
      documentKey: newer.binding.documentKey,
      kind: 'changed',
      sha256: newer.binding.sha256,
      modifiedAtMs: newer.binding.modifiedAtMs
    })

    expect(await screen.findByText('This file changed outside JobOS.')).not.toBeNull()
    await act(async () => { resolveRestore(restored); await Promise.resolve() })
    await waitFor(() => {
      expect(screen.queryByText('(FAKE)-restored-older.docx')).toBeNull()
      expect(screen.getByText('(FAKE)-polished-resume.docx')).not.toBeNull()
      expect(screen.getByText('This file changed outside JobOS.')).not.toBeNull()
    })
  })

  it('keeps an edit made while an external reload is parsing and enters conflict state', async () => {
    const opened = await fixture()
    let resolveReload!: (value: DocxOpenResult) => void
    const { bridge, emit } = installBridge(opened)
    render(<DocxDocumentEditorShell jobLabel="(FAKE) Northstar · Product Lead" onExit={vi.fn()} onPrepareClose={vi.fn()} opened={opened} />)
    await screen.findByRole('toolbar', { name: 'DOCX formatting' })
    bridge.reload.mockImplementationOnce(() => new Promise(resolve => { resolveReload = resolve }))

    emit({
      bindingId: opened.binding.bindingId,
      jobId: opened.binding.jobId,
      documentKey: 'resume',
      kind: 'changed',
      sha256: 'b'.repeat(64),
      modifiedAtMs: 2
    })
    await waitFor(() => expect(bridge.reload).toHaveBeenCalledTimes(2))
    insertEditorText('(FAKE) local edit during reload ')
    await screen.findByText('Unsaved changes')
    resolveReload({
      ...opened,
      binding: { ...opened.binding, sha256: 'b'.repeat(64), revision: 2, modifiedAtMs: 2 }
    })

    expect(await screen.findByText('This file changed outside JobOS.')).not.toBeNull()
    expect(document.querySelector('.ProseMirror')?.textContent).toContain('(FAKE) local edit during reload')
  })

  it('stops automatic retries after a persistent save error', async () => {
    const opened = await fixture()
    const { bridge } = installBridge(opened)
    bridge.save.mockRejectedValue(new Error('Disk is full'))
    render(<DocxDocumentEditorShell jobLabel="(FAKE) Northstar · Product Lead" onExit={vi.fn()} onPrepareClose={vi.fn()} opened={opened} />)
    await screen.findByRole('toolbar', { name: 'DOCX formatting' })

    insertEditorText('(FAKE) save failure ')
    await waitFor(() => expect(bridge.save).toHaveBeenCalledTimes(1), { timeout: 2_000 })
    await new Promise(resolve => setTimeout(resolve, 100))

    expect(bridge.save).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Disk is full')).not.toBeNull()
  })

  it('shows explicit recovery choices when the canonical file disappears externally', async () => {
    const opened = await fixture()
    const { emit } = installBridge(opened)
    render(<DocxDocumentEditorShell jobLabel="(FAKE) Northstar · Product Lead" onExit={vi.fn()} onPrepareClose={vi.fn()} opened={opened} />)
    await screen.findByRole('toolbar', { name: 'DOCX formatting' })

    emit({ bindingId: opened.binding.bindingId, jobId: opened.binding.jobId, documentKey: 'resume', kind: 'missing' })

    expect(await screen.findByText('This file changed outside JobOS.')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Reload External Version' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Save Mine As…' })).not.toBeNull()
  })
})
