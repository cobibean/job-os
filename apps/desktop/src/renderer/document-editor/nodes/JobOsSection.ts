import { Node, mergeAttributes } from '@tiptap/core'

export const JobOsSection = Node.create({
  name: 'jobosSection', group: 'block', content: 'block+', defining: true,
  addAttributes() { return { jobosId: { default: null }, semanticRole: { default: null }, label: { default: '' }, locked: { default: false }, origin: { default: 'user' }, structuralSuggestion: { default: null } } },
  parseHTML() { return [{ tag: 'section[data-jobos-section]' }] },
  renderHTML({ HTMLAttributes }) { return ['section', mergeAttributes(HTMLAttributes, { 'data-jobos-section': '' }), 0] }
})
