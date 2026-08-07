import { Mark, mergeAttributes } from '@tiptap/core'
export const Suggestion = Mark.create({
  name: 'suggestion', inclusive: false,
  addAttributes() { return { suggestionId: { default: null }, kind: { default: 'insert' }, author: { default: 'user' }, createdAt: { default: null } } },
  parseHTML() { return [{ tag: 'span[data-jobos-suggestion]' }] },
  renderHTML({ HTMLAttributes }) { return ['span', mergeAttributes(HTMLAttributes, { 'data-jobos-suggestion': '' }), 0] }
})
