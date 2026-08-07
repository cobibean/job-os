import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { JobOsRendererBridge } from '../../shared/contracts'
import type { EditableDocument, SaveEditableDocumentRequest } from '../../shared/editableDocuments'
import { createBlankDocument, defaultDocumentSettings, validateEditableContent } from '../../shared/editableDocumentSchema'
import { DocumentEditorShell } from './DocumentEditorShell'

function editableDocument(): EditableDocument {
  return {
    schemaVersion: 1,
    documentId: 'edoc_ABCDEFGHIJKLMNOPQRSTUVWX',
    jobId: 'job-7',
    documentKey: 'resume',
    documentLabel: 'Resume',
    revision: 1,
    content: createBlankDocument('resume'),
    settings: defaultDocumentSettings(),
    comments: [],
    sourceArtifactId: null,
    sourceFilename: null,
    sourceSha256: null,
    publishedRevision: null,
    importReport: { sourceFilename: null, importedAt: null, issues: [] },
    createdAt: '2026-08-07T00:00:00Z',
    updatedAt: '2026-08-07T00:00:00Z'
  }
}

function installEditorBridge() {
  const original = editableDocument()
  const save = vi.fn(async (_documentId: string, request: SaveEditableDocumentRequest) => ({
    ...original,
    revision: request.baseRevision + 1,
    content: request.content,
    settings: request.settings,
    comments: request.comments,
    updatedAt: '2026-08-07T00:01:00Z'
  }))
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { editableDocuments: { save } } as unknown as JobOsRendererBridge
  })
  return save
}

afterEach(() => {
  cleanup()
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('immersive document editor', () => {
  it('renders the Word-like editor, complete ribbon, views, inspector, and status', async () => {
    installEditorBridge()
    render(<DocumentEditorShell document={editableDocument()} jobLabel="Northstar · Product Lead" onDocumentChange={vi.fn()} onExit={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Back to Review' })).not.toBeNull()
    expect(screen.getByRole('toolbar', { name: 'Document formatting' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Bold (⌘B)' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Page break (⌘↵)' })).not.toBeNull()
    expect(screen.getByRole('tab', { name: 'Edit' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: 'Page setup' })).not.toBeNull()
    expect(await screen.findByTestId('document-page-canvas')).not.toBeNull()
    expect(screen.getByText('0 words')).not.toBeNull()
  })

  it('autosaves page-setting changes and flushes before returning to Review', async () => {
    const save = installEditorBridge()
    const onDocumentChange = vi.fn()
    const onExit = vi.fn()
    render(<DocumentEditorShell document={editableDocument()} jobLabel="Northstar · Product Lead" onDocumentChange={onDocumentChange} onExit={onExit} />)

    fireEvent.click(screen.getByRole('tab', { name: 'Page setup' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Page size' }), { target: { value: 'a4' } })
    expect(screen.getAllByText('Unsaved changes')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Back to Review' }))

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0]?.[1].settings.pageSize).toBe('a4')
    await waitFor(() => expect(onDocumentChange).toHaveBeenCalled())
    await waitFor(() => expect(onExit).toHaveBeenCalledTimes(1))
  })

  it('keeps export and publish gated when unresolved suggestions exist', () => {
    installEditorBridge()
    const document = editableDocument()
    const firstParagraph = document.content.content?.[0]?.content?.[0]
    if (!firstParagraph) throw new Error('Fixture paragraph missing')
    firstParagraph.content = [{
      type: 'text',
      text: 'Suggested text',
      marks: [{
        type: 'suggestion',
        attrs: {
          suggestionId: 'sug_one',
          kind: 'insert',
          author: 'user',
          createdAt: '2026-08-07T00:00:00Z'
        }
      }]
    }]
    render(<DocumentEditorShell document={document} jobLabel="Northstar · Product Lead" onDocumentChange={vi.fn()} onExit={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'DOCX' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: 'PDF' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: 'Publish revision' }).hasAttribute('disabled')).toBe(true)
  })

  it('uses authoritative PDF bytes for the generated print preview', async () => {
    installEditorBridge()
    const preview = vi.fn(async () => ({
      documentId: editableDocument().documentId,
      revision: 1,
      filename: 'Resume-r1.pdf',
      sha256: 'a'.repeat(64),
      bytes: new TextEncoder().encode('%PDF-preview').buffer
    }))
    Object.assign(window.jobos!.editableDocuments, { preview })
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:jobos-preview'),
      revokeObjectURL: vi.fn()
    })

    render(<DocumentEditorShell document={editableDocument()} jobLabel="Northstar · Product Lead" onDocumentChange={vi.fn()} onExit={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))

    await waitFor(() => expect(preview).toHaveBeenCalledWith('edoc_ABCDEFGHIJKLMNOPQRSTUVWX'))
    expect((await screen.findByTitle('Generated PDF preview')).getAttribute('src')).toBe('blob:jobos-preview')
  })

  it('records page breaks inserted in suggesting mode as structural suggestions', async () => {
    const save = installEditorBridge()
    render(<DocumentEditorShell document={editableDocument()} jobLabel="Northstar · Product Lead" onDocumentChange={vi.fn()} onExit={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Suggesting' }))
    fireEvent.click(screen.getByRole('button', { name: 'Page break (⌘↵)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back to Review' }))

    await waitFor(() => expect(save).toHaveBeenCalled())
    const serialized = JSON.stringify(save.mock.calls.at(-1)?.[1].content)
    expect(serialized).toContain('pageBreak')
    expect(serialized).toContain('structuralSuggestion')
    expect(serialized).toContain('"kind":"insert"')
  })

  it('serializes suggested table insertions into valid canonical content', async () => {
    const save = installEditorBridge()
    render(<DocumentEditorShell document={editableDocument()} jobLabel="Northstar · Product Lead" onDocumentChange={vi.fn()} onExit={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Suggesting' }))
    fireEvent.click(screen.getByRole('button', { name: 'Table' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back to Review' }))

    await waitFor(() => expect(save).toHaveBeenCalled())
    const content = save.mock.calls.at(-1)?.[1].content
    expect(content).toBeDefined()
    expect(() => validateEditableContent(content!)).not.toThrow()
    expect(JSON.stringify(content)).toContain('"structuralSuggestion":{"suggestionId":"sug_')
  })
})
