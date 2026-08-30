import { EditorContent, useEditor } from '@tiptap/react'
import { useEffect, useMemo } from 'react'

import type { DocumentSettings, TiptapDocumentJson } from '../../../../shared/editableDocuments'
import { createDocumentExtensions } from './extensions'
import { applyPaginationSettings } from './paginationAdapter'
import { DocumentRibbon } from './DocumentRibbon'

interface DocumentEditorProps {
  content: TiptapDocumentJson
  documentRevision: number
  onChange: (content: TiptapDocumentJson) => void
  onSelectedBlockChange: (blockId: `node_${string}` | null) => void
  settings: DocumentSettings
}

export function DocumentEditor({ content, documentRevision, onChange, onSelectedBlockChange, settings }: DocumentEditorProps) {
  const extensions = useMemo(() => createDocumentExtensions({ pagination: settings }), [])
  const editor = useEditor({
    content,
    extensions,
    immediatelyRender: false,
    editorProps: {
      attributes: {
        'aria-label': 'Document content',
        class: 'jobos-prosemirror',
        spellcheck: 'true'
      }
    },
    onUpdate: ({ editor: currentEditor }) => {
      onChange(currentEditor.getJSON() as TiptapDocumentJson)
    },
    onSelectionUpdate: ({ editor: currentEditor }) => {
      const selection = currentEditor.state.selection.$from
      for (let depth = selection.depth; depth > 0; depth -= 1) {
        const blockId = selection.node(depth).attrs.jobosId
        if (typeof blockId === 'string' && blockId.startsWith('node_')) {
          onSelectedBlockChange(blockId as `node_${string}`)
          return
        }
      }
      onSelectedBlockChange(null)
    }
  })

  useEffect(() => {
    if (!editor) return
    const next = JSON.stringify(content)
    if (JSON.stringify(editor.getJSON()) !== next) editor.commands.setContent(content, { emitUpdate: false })
  }, [content, documentRevision, editor])

  useEffect(() => {
    if (editor) applyPaginationSettings(editor, settings)
  }, [editor, settings])

  if (!editor) return <div className="document-editor-loading" role="status">Preparing editor…</div>

  return (
    <>
      <DocumentRibbon editor={editor} />
      <div className="document-page-scroll">
        <article className="document-page-canvas" data-testid="document-page-canvas">
          <EditorContent editor={editor} />
        </article>
      </div>
    </>
  )
}
