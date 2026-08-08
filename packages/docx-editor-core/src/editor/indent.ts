import type { Editor } from '@tiptap/core'

/** Word's ribbon indent step: half an inch, in twips */
const STEP = 720

/** Ribbon increase/decrease indent: list items change level, plain paragraphs and headings snap to the next half-inch stop */
export function stepParagraphIndent(editor: Editor, delta: 1 | -1): boolean {
  if (editor.isActive('docListItem')) {
    const ilvl = Number(editor.getAttributes('docListItem').ilvl) || 0
    const next = Math.min(Math.max(ilvl + delta, 0), 8)
    return editor.chain().focus().updateAttributes('docListItem', { ilvl: next }).run()
  }
  return editor
    .chain()
    .focus()
    .command(({ state, tr, dispatch }) => {
      const { from, to } = state.selection
      let changed = false
      state.doc.nodesBetween(from, to, (node, pos) => {
        if (node.type.name !== 'docParagraph' && node.type.name !== 'docHeading') return
        const cur = Number(node.attrs.indentLeft) || 0
        const next =
          delta > 0
            ? Math.floor(cur / STEP) * STEP + STEP
            : Math.max(Math.ceil(cur / STEP) * STEP - STEP, 0)
        if (next === cur) return
        tr.setNodeMarkup(pos, undefined, { ...node.attrs, indentLeft: next || null })
        changed = true
      })
      if (changed && dispatch) dispatch(tr)
      return changed
    })
    .run()
}
