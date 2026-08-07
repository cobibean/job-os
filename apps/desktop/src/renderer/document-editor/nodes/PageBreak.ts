import { Node, mergeAttributes } from '@tiptap/core'
export const PageBreak = Node.create({
  name: 'pageBreak', group: 'block', atom: true,
  addAttributes() { return { jobosId: { default: null }, semanticRole: { default: null }, locked: { default: false }, origin: { default: 'user' }, structuralSuggestion: { default: null } } },
  parseHTML() { return [{ tag: 'div[data-jobos-page-break]' }] },
  renderHTML({ HTMLAttributes }) { return ['div', mergeAttributes(HTMLAttributes, { 'data-jobos-page-break': '', role: 'separator' })] }
})
