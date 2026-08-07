import { Mark, mergeAttributes } from '@tiptap/core'
export const JobOsField = Mark.create({
  name: 'jobosField', inclusive: false,
  addAttributes() { return { fieldType: { default: 'custom' }, locked: { default: true } } },
  parseHTML() { return [{ tag: 'span[data-jobos-field]' }] },
  renderHTML({ HTMLAttributes }) { return ['span', mergeAttributes(HTMLAttributes, { 'data-jobos-field': '' }), 0] }
})
