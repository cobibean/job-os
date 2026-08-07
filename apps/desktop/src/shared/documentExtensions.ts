import { Extension, Mark, Node, mergeAttributes } from '@tiptap/core'
import Image from '@tiptap/extension-image'
import { TableKit } from '@tiptap/extension-table'
import TextAlign from '@tiptap/extension-text-align'
import { TextStyleKit } from '@tiptap/extension-text-style'
import UniqueID from '@tiptap/extension-unique-id'
import StarterKit from '@tiptap/starter-kit'

import { JOBOS_BLOCK_TYPES } from './editableDocumentSchema.js'

const globalBlockAttributes = {
  jobosId: { default: null },
  semanticRole: { default: null },
  locked: { default: false },
  origin: { default: 'user' },
  structuralSuggestion: { default: null }
}

export const JobOsBlockAttributes = Extension.create({
  name: 'jobosBlockAttributes',
  addGlobalAttributes() {
    return [{
      types: ['paragraph', 'heading', 'listItem', 'blockquote', 'horizontalRule', 'table', 'image'],
      attributes: globalBlockAttributes
    }]
  }
})

export const JobOsDocument = Node.create({
  name: 'doc',
  topNode: true,
  content: 'jobosSection+'
})

export const JobOsSection = Node.create({
  name: 'jobosSection',
  group: 'block',
  content: 'block+',
  defining: true,
  addAttributes() {
    return {
      jobosId: { default: null },
      semanticRole: { default: null },
      label: { default: '' },
      locked: { default: false },
      origin: { default: 'user' },
      structuralSuggestion: { default: null }
    }
  },
  parseHTML() { return [{ tag: 'section[data-jobos-section]' }] },
  renderHTML({ HTMLAttributes }) {
    return ['section', mergeAttributes(HTMLAttributes, { 'data-jobos-section': '' }), 0]
  }
})

export const PageBreak = Node.create({
  name: 'pageBreak',
  group: 'block',
  atom: true,
  addAttributes() {
    return {
      jobosId: { default: null },
      semanticRole: { default: null },
      locked: { default: false },
      origin: { default: 'user' },
      structuralSuggestion: { default: null }
    }
  },
  parseHTML() { return [{ tag: 'div[data-jobos-page-break]' }] },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, {
      'data-jobos-page-break': '',
      role: 'separator'
    })]
  }
})

export const JobOsField = Mark.create({
  name: 'jobosField',
  inclusive: false,
  addAttributes() {
    return { fieldType: { default: 'custom' }, locked: { default: true } }
  },
  parseHTML() { return [{ tag: 'span[data-jobos-field]' }] },
  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes, { 'data-jobos-field': '' }), 0]
  }
})

export const Suggestion = Mark.create({
  name: 'suggestion',
  inclusive: false,
  addAttributes() {
    return {
      suggestionId: { default: null },
      kind: { default: 'insert' },
      author: { default: 'user' },
      createdAt: { default: null }
    }
  },
  parseHTML() { return [{ tag: 'span[data-jobos-suggestion]' }] },
  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes, { 'data-jobos-suggestion': '' }), 0]
  }
})

/** Runtime-safe schema shared by Electron main conversion and the renderer editor. */
export function createCoreDocumentExtensions() {
  return [
    StarterKit.configure({
      document: false,
      code: false,
      codeBlock: false,
      heading: { levels: [1, 2, 3] }
    }),
    TextStyleKit,
    TextAlign.configure({
      types: ['heading', 'paragraph'],
      alignments: ['left', 'center', 'right', 'justify']
    }),
    TableKit,
    Image.configure({ allowBase64: true, inline: false, resize: false }),
    JobOsBlockAttributes,
    JobOsDocument,
    JobOsSection,
    PageBreak,
    JobOsField,
    Suggestion,
    UniqueID.configure({
      attributeName: 'jobosId',
      types: [...JOBOS_BLOCK_TYPES],
      generateID: () => `node_${globalThis.crypto.randomUUID()}`,
      updateDocument: true
    })
  ]
}
