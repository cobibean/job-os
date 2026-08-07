import { Extension } from '@tiptap/core'

import Image from '@tiptap/extension-image'
import Placeholder from '@tiptap/extension-placeholder'
import { TableKit } from '@tiptap/extension-table'
import TextAlign from '@tiptap/extension-text-align'
import { TextStyleKit } from '@tiptap/extension-text-style'
import UniqueID from '@tiptap/extension-unique-id'
import StarterKit from '@tiptap/starter-kit'

import type { DocumentSettings } from '../../shared/editableDocuments.js'
import { JOBOS_BLOCK_TYPES } from '../../shared/editableDocumentSchema.js'
import { JobOsField } from './marks/JobOsField.js'
import { Suggestion } from './marks/Suggestion.js'
import { JobOsDocument } from './nodes/JobOsDocument.js'
import { JobOsSection } from './nodes/JobOsSection.js'
import { PageBreak } from './nodes/PageBreak.js'
import { createPaginationExtension } from './paginationAdapter.js'

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

/** The one schema factory shared by editor, main-process conversion, and print rendering. */
export function createDocumentExtensions(options: { pagination?: DocumentSettings } = {}) {
  return [
    StarterKit.configure({ document: false, code: false, codeBlock: false, heading: { levels: [1, 2, 3] } }),
    TextStyleKit,
    TextAlign.configure({ types: ['heading', 'paragraph'], alignments: ['left', 'center', 'right', 'justify'] }),

    TableKit,
    Image.configure({ allowBase64: true, inline: false, resize: false }),
    Placeholder.configure({
      placeholder: ({ node }) => node.type.name === 'jobosSection' ? String(node.attrs.label ?? '') : ''
    }),
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
    }),
    ...(options.pagination ? [createPaginationExtension(options.pagination)] : [])
  ]
}
