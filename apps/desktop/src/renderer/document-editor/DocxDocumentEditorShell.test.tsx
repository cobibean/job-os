import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { JobOsRendererBridge } from '../../shared/contracts'
import type { DocxBinding, DocxExternalChangeEvent, DocxOpenResult } from '../../shared/docxDocuments'
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
    listRecoveries: vi.fn(async () => []),
    reload: vi.fn(async () => opened),
    save: vi.fn(),
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

  it('keeps an edit made while an external reload is parsing and enters conflict state', async () => {
    const opened = await fixture()
    let resolveReload!: (value: DocxOpenResult) => void
    const { bridge, emit } = installBridge(opened)
    bridge.reload.mockImplementation(() => new Promise(resolve => { resolveReload = resolve }))
    render(<DocxDocumentEditorShell jobLabel="(FAKE) Northstar · Product Lead" onExit={vi.fn()} onPrepareClose={vi.fn()} opened={opened} />)
    await screen.findByRole('toolbar', { name: 'DOCX formatting' })

    emit({
      bindingId: opened.binding.bindingId,
      jobId: opened.binding.jobId,
      documentKey: 'resume',
      kind: 'changed',
      sha256: 'b'.repeat(64),
      modifiedAtMs: 2
    })
    await waitFor(() => expect(bridge.reload).toHaveBeenCalledTimes(1))
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
