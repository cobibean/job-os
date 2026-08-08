import type { Editor } from '@tiptap/core'
import type { DocDefaults, StyleInfo } from '@jobos/docx-engine'

const TEXT_BLOCK_TYPES = new Set(['docParagraph', 'docHeading', 'docListItem'])

function styleSize(styleId: unknown, styles: Map<string, StyleInfo> | undefined): number | null {
  if (typeof styleId !== 'string' || !styleId) return null
  const size = styles?.get(styleId)?.display?.sizeHalfPoints
  return typeof size === 'number' && size > 0 ? size : null
}

/** Resolve the font size Word displays at the caret without adding direct formatting. */
export function effectiveSizeHalfPoints(
  editor: Editor,
  styles?: Map<string, StyleInfo>,
  docDefaults?: DocDefaults,
): number | null {
  const textAttrs = editor.getAttributes('docTextStyle')
  const directSize = Number(textAttrs.sizeHalfPoints)
  if (Number.isFinite(directSize) && directSize > 0) return directSize

  const characterStyleSize = styleSize(textAttrs.styleId, styles)
  if (characterStyleSize !== null) return characterStyleSize

  const { $from } = editor.state.selection
  for (let depth = $from.depth; depth > 0; depth -= 1) {
    const node = $from.node(depth)
    if (!TEXT_BLOCK_TYPES.has(node.type.name)) continue
    const paragraphStyleSize = styleSize(node.attrs.styleId, styles)
    if (paragraphStyleSize !== null) return paragraphStyleSize
    break
  }

  const defaultSize = docDefaults?.sizeHalfPoints
  return typeof defaultSize === 'number' && defaultSize > 0 ? defaultSize : null
}
