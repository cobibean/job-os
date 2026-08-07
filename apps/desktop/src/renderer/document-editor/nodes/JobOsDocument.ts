import { Node } from '@tiptap/core'

/** Canonical JobOS root: documents are composed only of persistent semantic sections. */
export const JobOsDocument = Node.create({
  name: 'doc',
  topNode: true,
  content: 'jobosSection+'
})
