import { createHash } from 'node:crypto'

import type { DocumentImportIssue, DocumentKey, SemanticRole, TiptapDocumentJson, TiptapMarkJson, TiptapNodeJson } from '../../../../shared/editableDocuments.js'
import { plainText, validateEditableContent } from '../../../../shared/editableDocumentSchema.js'

const BLOCKS = new Set(['jobosSection', 'paragraph', 'heading', 'listItem', 'blockquote', 'horizontalRule', 'pageBreak', 'table', 'image'])
const MARKS = new Set(['bold', 'italic', 'underline', 'strike', 'textStyle', 'link'])

function stableId(seed: string): `node_${string}` {
  const hex = createHash('sha256').update(seed).digest('hex').slice(0, 32)
  const uuid = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-5${hex.slice(13, 16)}-${((Number.parseInt(hex[16] ?? '0', 16) & 3) | 8).toString(16)}${hex.slice(17, 20)}-${hex.slice(20)}`
  return `node_${uuid}`
}

function semanticRole(label: string, key: DocumentKey): SemanticRole {
  const value = label.toLowerCase()
  if (/contact|profile|personal|header/.test(value)) return 'contact'
  if (/summary|objective|professional profile/.test(value)) return 'summary'
  if (/experience|employment|work history/.test(value)) return 'experience'
  if (/education|certification/.test(value)) return 'education'
  if (/skill|competenc|technology/.test(value)) return 'skills'
  if (/reference/.test(value)) return 'reference'
  if (/closing|signature/.test(value)) return 'closing'
  if (/cover|body|letter/.test(value)) return 'cover_letter_body'
  return key === 'cover_letter' ? 'cover_letter_body' : key === 'references' ? 'reference' : 'custom'
}

function safeMarks(marks: TiptapMarkJson[] | undefined): TiptapMarkJson[] | undefined {
  const result = (marks ?? []).flatMap(mark => {
    if (!MARKS.has(mark.type)) return []
    if (['bold', 'italic', 'underline', 'strike'].includes(mark.type)) return [{ type: mark.type }]
    if (mark.type === 'link') {
      const href = mark.attrs?.href
      return typeof href === 'string' && /^(https?:|mailto:)/i.test(href)
        ? [{ type: 'link', attrs: { href, target: null, rel: 'noopener noreferrer nofollow', class: null } }]
        : []
    }
    const attrs = mark.attrs ?? {}
    const clean: Record<string, unknown> = {}
    for (const key of ['fontFamily', 'fontSize', 'lineHeight', 'color', 'backgroundColor']) {
      if (attrs[key] !== undefined && attrs[key] !== null) clean[key] = attrs[key]
    }
    return Object.keys(clean).length ? [{ type: 'textStyle', attrs: clean }] : []
  })
  return result.length ? result : undefined
}

function canonicalNode(node: TiptapNodeJson): TiptapNodeJson | null {
  if (node.type === 'text') return typeof node.text === 'string' && node.text.length ? { type: 'text', text: node.text, ...(safeMarks(node.marks) ? { marks: safeMarks(node.marks) } : {}) } : null
  const allowed = new Set(['doc', 'paragraph', 'heading', 'bulletList', 'orderedList', 'listItem', 'blockquote', 'horizontalRule', 'hardBreak', 'table', 'tableRow', 'tableHeader', 'tableCell', 'image'])
  if (!allowed.has(node.type)) return null
  const content = (node.content ?? []).map(canonicalNode).filter((child): child is TiptapNodeJson => child !== null)
  if (node.type === 'paragraph' && content.length === 0) return null
  const result: TiptapNodeJson = { type: node.type }
  if ((node.type === 'tableCell' || node.type === 'tableHeader') && content.length === 0) {
    result.content = [{ type: 'paragraph', content: [] }]
  } else if (content.length || ['paragraph', 'tableCell', 'tableHeader', 'listItem'].includes(node.type)) {
    result.content = content
  }
  if (node.type === 'heading') result.attrs = { level: Math.min(3, Math.max(1, Number(node.attrs?.level) || 1)), ...(node.attrs?.textAlign ? { textAlign: node.attrs.textAlign } : {}) }
  if (node.type === 'paragraph' && node.attrs?.textAlign) result.attrs = { textAlign: node.attrs.textAlign }
  if (node.type === 'orderedList' && Number.isInteger(node.attrs?.start)) result.attrs = { start: node.attrs?.start }
  if (node.type === 'tableCell' || node.type === 'tableHeader') {
    const attrs: Record<string, unknown> = {}
    for (const key of ['colspan', 'rowspan', 'colwidth', 'backgroundColor', 'align']) if (node.attrs?.[key] !== undefined && node.attrs[key] !== null) attrs[key] = node.attrs[key]
    if (Object.keys(attrs).length) result.attrs = attrs
  }
  if (node.type === 'image') {
    const src = node.attrs?.src
    if (typeof src !== 'string' || !/^data:image\/(png|jpeg|gif);base64,/.test(src)) return null
    result.attrs = Object.fromEntries(['src', 'alt', 'title', 'width', 'height'].flatMap(key => node.attrs?.[key] === undefined || node.attrs[key] === null ? [] : [[key, node.attrs[key]]]))
  }
  return result
}

function insertPageBreaks(content: TiptapNodeJson[], positions: Set<number>): void {
  let paragraphIndex = -1
  const walk = (nodes: TiptapNodeJson[]): void => {
    for (let index = 0; index < nodes.length; index += 1) {
      const node = nodes[index]
      if (!node) continue
      if (node.type === 'paragraph') {
        paragraphIndex += 1
        if (positions.has(paragraphIndex)) {
          nodes.splice(index + 1, 0, { type: 'pageBreak' })
          index += 1
        }
      }
      if (node.content) walk(node.content)
    }
  }
  walk(content)
}

function attrs(id: `node_${string}`, role: SemanticRole, label?: string) {
  return { jobosId: id, semanticRole: role, locked: role === 'contact' || role === 'closing', origin: 'import', structuralSuggestion: null, ...(label ? { label } : {}) }
}

export interface NormalizeOptions {
  documentKey: DocumentKey
  explicitPageBreakAfterParagraphs: number[]
  issues: DocumentImportIssue[]
  sourceFilename: string
  importedAt: string
}

export function normalizeImportedDocument(raw: TiptapDocumentJson, options: NormalizeOptions): TiptapDocumentJson {
  const canonical = canonicalNode(raw)
  if (!canonical || canonical.type !== 'doc') throw new Error('DOCX conversion did not produce a document')
  const top = canonical.content ?? []
  insertPageBreaks(top, new Set(options.explicitPageBreakAfterParagraphs))

  const groups: Array<{ label: string; role: SemanticRole; nodes: TiptapNodeJson[] }> = []
  for (const node of top) {
    if (node.type === 'heading') {
      const label = plainText(node).trim().slice(0, 120) || 'Imported section'
      groups.push({ label, role: semanticRole(label, options.documentKey), nodes: [node] })
    } else {
      if (!groups.length) {
        const label = options.documentKey === 'cover_letter' ? 'Body' : options.documentKey === 'references' ? 'References' : 'Contact'
        groups.push({ label, role: semanticRole(label, options.documentKey), nodes: [] })
      }
      groups[groups.length - 1]?.nodes.push(node)
    }
  }
  if (!groups.length) throw new Error('DOCX contains no supported visible content')

  let ordinal = 0
  const assign = (node: TiptapNodeJson, role: SemanticRole): void => {
    if (BLOCKS.has(node.type)) {
      node.attrs = { ...node.attrs, ...attrs(stableId(`${options.sourceFilename}:${ordinal}:${node.type}:${plainText(node).slice(0, 200)}`), role) }
      ordinal += 1
    }
    for (const child of node.content ?? []) assign(child, role)
  }
  const content = groups.map(group => {
    const section: TiptapNodeJson = {
      type: 'jobosSection',
      attrs: attrs(stableId(`${options.sourceFilename}:section:${group.label}:${ordinal++}`), group.role, group.label),
      content: group.nodes.length ? group.nodes : [{ type: 'paragraph', content: [] }]
    }
    for (const child of section.content ?? []) assign(child, group.role)
    return section
  })
  const normalized: TiptapDocumentJson = { type: 'doc', content }
  validateEditableContent(normalized, undefined, [], {
    sourceFilename: options.sourceFilename,
    importedAt: options.importedAt,
    issues: options.issues
  })
  return normalized
}
