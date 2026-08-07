import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { EditableDocument, EditableDocumentSnapshot } from '../../shared/editableDocuments'
import { createBlankDocument, defaultDocumentSettings } from '../../shared/editableDocumentSchema'
import { DocumentInspector } from './DocumentInspector'

function documentFixture(): EditableDocument {
  return {
    schemaVersion: 1,
    documentId: 'edoc_ABCDEFGHIJKLMNOPQRSTUVWX',
    jobId: 'job-7',
    documentKey: 'references',
    documentLabel: 'References',
    revision: 3,
    content: createBlankDocument('references'),
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

function inspectorProps(document = documentFixture()) {
  return {
    comments: document.comments,
    content: document.content,
    document,
    historyBusy: false,
    onCommentsChange: vi.fn(),
    onCreateSnapshot: vi.fn(),
    onResolveSuggestion: vi.fn(),
    onRestoreSnapshot: vi.fn(),
    onSettingsChange: vi.fn(),
    onTabChange: vi.fn(),
    onToggleSelectedBlockLock: vi.fn(),
    selectedBlockId: document.content.content?.[1]?.content?.[0]?.attrs?.jobosId as `node_${string}`,
    selectedBlockLocked: false,
    settings: document.settings,
    snapshots: [] as EditableDocumentSnapshot[]
  }
}

afterEach(cleanup)

describe('document review inspector', () => {
  it('adds and resolves stable-block comments', () => {
    const document = documentFixture()
    const props = inspectorProps(document)
    const { rerender } = render(<DocumentInspector {...props} activeTab="comments" />)

    fireEvent.change(screen.getByRole('textbox', { name: 'Comment text' }), { target: { value: 'Clarify this result.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }))
    expect(props.onCommentsChange).toHaveBeenCalledTimes(1)
    const comments = props.onCommentsChange.mock.calls[0]?.[0]
    expect(comments[0]).toMatchObject({ blockId: props.selectedBlockId, author: 'user', body: 'Clarify this result.', resolvedAt: null })

    const resolveProps = { ...props, comments, onCommentsChange: vi.fn() }
    rerender(<DocumentInspector {...resolveProps} activeTab="comments" />)
    fireEvent.click(screen.getByRole('button', { name: 'Resolve' }))
    expect(resolveProps.onCommentsChange.mock.calls[0]?.[0][0].resolvedAt).toMatch(/^\d{4}-/)
  })

  it('exposes each unresolved suggestion with explicit accept and reject actions', () => {
    const document = documentFixture()
    const block = document.content.content?.[1]?.content?.[0]
    if (!block) throw new Error('Fixture block missing')
    block.content = [{ type: 'text', text: 'Suggested reference', marks: [{ type: 'suggestion', attrs: { suggestionId: 'sug_review', kind: 'insert', author: 'user', createdAt: '2026-08-07T00:00:00Z' } }] }]
    const props = inspectorProps(document)
    render(<DocumentInspector {...props} activeTab="comments" />)

    expect(screen.getByText('Suggested reference')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }))
    expect(props.onResolveSuggestion).toHaveBeenCalledWith(expect.objectContaining({ suggestionId: 'sug_review' }), 'accept')
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
    expect(props.onResolveSuggestion).toHaveBeenCalledWith(expect.objectContaining({ suggestionId: 'sug_review' }), 'reject')
  })

  it('lists persisted snapshots with checkpoint and restore controls', () => {
    const document = documentFixture()
    const snapshot: EditableDocumentSnapshot = {
      snapshotId: 'dsnap_ABCDEFGHIJKLMNOPQRSTUVWX',
      documentId: document.documentId,
      documentRevision: 2,
      reason: 'manual',
      actor: 'user',
      label: 'Before tailoring',
      createdAt: '2026-08-07T00:00:00Z'
    }
    const props = { ...inspectorProps(document), snapshots: [snapshot] }
    render(<DocumentInspector {...props} activeTab="history" />)

    expect(screen.getByText('Revision 2 · manual')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Create checkpoint' }))
    expect(props.onCreateSnapshot).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }))
    expect(props.onRestoreSnapshot).toHaveBeenCalledWith(snapshot)
  })
})
