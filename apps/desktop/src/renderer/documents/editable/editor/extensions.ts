import Placeholder from '@tiptap/extension-placeholder'

import type { DocumentSettings } from '../../../../shared/editableDocuments.js'
import { createCoreDocumentExtensions } from '../../../../shared/documentExtensions.js'
import { createPaginationExtension } from './paginationAdapter.js'

/** Renderer schema: shared canonical extensions plus UI-only placeholder and pagination behavior. */
export function createDocumentExtensions(options: { pagination?: DocumentSettings } = {}) {
  return [
    ...createCoreDocumentExtensions(),
    Placeholder.configure({
      placeholder: ({ node }) => node.type.name === 'jobosSection' ? String(node.attrs.label ?? '') : ''
    }),
    ...(options.pagination ? [createPaginationExtension(options.pagination)] : [])
  ]
}
