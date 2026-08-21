import type {
  DocumentComment,
  DocumentImportReport,
  DocumentKey,
  DocumentSettings,
  SemanticOutlineBlock,
  SemanticRole,
  StructuralSuggestion,
  TiptapDocumentJson,
  TiptapMarkJson,
  TiptapNodeJson
} from './editableDocuments.js'

export const MAX_CANONICAL_BYTES = 8 * 1024 * 1024
export const MAX_BLOCKS = 5_000
export const MAX_TABLE_ROWS = 50
export const MAX_TABLE_COLUMNS = 20
export const MAX_COMMENTS = 200
export const MAX_COMMENT_CHARACTERS = 2_000
export const MAX_IMAGES = 20
export const MAX_IMAGE_BYTES = 2 * 1024 * 1024

export const JOBOS_BLOCK_TYPES = [
  'jobosSection',
  'paragraph',
  'heading',
  'listItem',
  'blockquote',
  'horizontalRule',
  'pageBreak',
  'table',
  'image'
] as const

const NODE_TYPES = new Set([
  'doc', 'jobosSection', 'paragraph', 'heading', 'bulletList', 'orderedList', 'listItem',
  'blockquote', 'horizontalRule', 'hardBreak', 'pageBreak', 'table', 'tableRow',
  'tableHeader', 'tableCell', 'image', 'text'
])
const MARK_TYPES = new Set(['bold', 'italic', 'underline', 'strike', 'textStyle', 'link', 'jobosField', 'suggestion'])
const ROLES = new Set<SemanticRole>([
  'contact', 'summary', 'experience', 'experience_achievement', 'education', 'skills',
  'reference', 'cover_letter_body', 'closing', 'custom'
])
const ACTORS = new Set(['user', 'jobhunter', 'import', 'system'])
const FONTS = new Set(['Arial', 'Calibri', 'Times New Roman', 'Georgia', 'Garamond'])
const BLOCKS = new Set<string>(JOBOS_BLOCK_TYPES)
const BLOCK_CONTENT = new Set([
  'jobosSection', 'paragraph', 'heading', 'bulletList', 'orderedList', 'blockquote',
  'horizontalRule', 'pageBreak', 'table', 'image'
])
const INLINE_CONTENT = new Set(['text', 'hardBreak'])
const ID = /^node_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const COMMENT_ID = /^comment_[A-Za-z0-9_-]{1,80}$/
const SUGGESTION_ID = /^sug_[A-Za-z0-9_-]{1,80}$/
const SAFE_LINK = /^(https?:|mailto:)/i
const IMAGE = /^data:image\/(png|jpeg|gif);base64,([A-Za-z0-9+/]*={0,2})$/
const HEX_COLOR = /^#[0-9a-f]{6}$/i
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/
const TOP_LEVEL_KEYS = new Set(['type', 'attrs', 'content', 'marks', 'text'])
const BLOCK_ATTRIBUTE_KEYS = ['jobosId', 'semanticRole', 'locked', 'origin', 'structuralSuggestion']
const ATTRIBUTE_KEYS: Record<string, Set<string>> = {
  doc: new Set(),
  jobosSection: new Set([...BLOCK_ATTRIBUTE_KEYS, 'label']),
  paragraph: new Set([...BLOCK_ATTRIBUTE_KEYS, 'textAlign']),
  heading: new Set([...BLOCK_ATTRIBUTE_KEYS, 'level', 'textAlign']),
  bulletList: new Set(),
  orderedList: new Set(['start']),
  listItem: new Set(BLOCK_ATTRIBUTE_KEYS),
  blockquote: new Set(BLOCK_ATTRIBUTE_KEYS),
  horizontalRule: new Set(BLOCK_ATTRIBUTE_KEYS),
  hardBreak: new Set(),
  pageBreak: new Set(BLOCK_ATTRIBUTE_KEYS),
  table: new Set(BLOCK_ATTRIBUTE_KEYS),
  tableRow: new Set(),
  tableHeader: new Set(['colspan', 'rowspan', 'colwidth', 'backgroundColor', 'align']),
  tableCell: new Set(['colspan', 'rowspan', 'colwidth', 'backgroundColor', 'align']),
  image: new Set([...BLOCK_ATTRIBUTE_KEYS, 'src', 'alt', 'title', 'width', 'height']),
  text: new Set()
}

export function defaultDocumentSettings(): DocumentSettings {
  return {
    pageSize: 'letter',
    orientation: 'portrait',
    marginsInches: { top: 1, right: 1, bottom: 1, left: 1 },
    defaultFontFamily: 'Calibri',
    defaultFontSizePt: 11,
    header: { left: '', center: '', right: '', firstPageDifferent: false },
    footer: { left: '', center: '', right: '', firstPageDifferent: false },
    showPageNumbers: false
  }
}

export function emptyImportReport(): DocumentImportReport {
  return { sourceFilename: null, importedAt: null, issues: [] }
}

function createNodeId(): `node_${string}` {
  return `node_${globalThis.crypto.randomUUID()}`
}

function blockAttrs(role: SemanticRole | null, locked = false, label?: string) {
  return {
    jobosId: createNodeId(),
    semanticRole: role,
    locked,
    origin: 'system',
    structuralSuggestion: null,
    ...(label === undefined ? {} : { label })
  }
}

function paragraph(role: SemanticRole | null = null, locked = false): TiptapNodeJson {
  return { type: 'paragraph', attrs: blockAttrs(role, locked), content: [] }
}

function section(label: string, role: SemanticRole, locked: boolean, count = 1): TiptapNodeJson {
  return {
    type: 'jobosSection',
    attrs: blockAttrs(role, locked, label),
    content: Array.from({ length: count }, () => paragraph(role, locked))
  }
}

export function createBlankDocument(key: DocumentKey): TiptapDocumentJson {
  const content = key === 'resume'
    ? [
        section('Contact', 'contact', true),
        section('Summary', 'summary', false),
        section('Experience', 'experience', false),
        section('Education', 'education', false),
        section('Skills', 'skills', false)
      ]
    : key === 'cover_letter'
      ? [
          section('Contact', 'contact', true),
          section('Body', 'cover_letter_body', false, 3),
          section('Closing', 'closing', true)
        ]
      : [section('Contact', 'contact', true), section('References', 'reference', false)]
  return { type: 'doc', content }
}

/** Clone a normalized/imported tree and assign IDs only to block nodes that lack one. */
export function assignMissingStableIds(
  content: TiptapDocumentJson,
  idFactory: () => `node_${string}` = createNodeId
): TiptapDocumentJson {
  const clone = structuredClone(content)
  const walk = (node: TiptapNodeJson): void => {
    if (BLOCKS.has(node.type)) {
      node.attrs ??= {}
      node.attrs.jobosId ??= idFactory()
    }
    for (const child of node.content ?? []) walk(child)
  }
  walk(clone)
  return clone
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(value: Record<string, unknown>, allowed: Set<string>, label: string): void {
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) throw new Error(`Unknown ${label} attribute: ${key}`)
  }
}

function validTimestamp(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 40 && ISO_TIMESTAMP.test(value) && Number.isFinite(Date.parse(value))
}

function validateHeaderFooter(value: unknown): void {
  if (!isRecord(value)) throw new Error('Invalid header or footer')
  exactKeys(value, new Set(['left', 'center', 'right', 'firstPageDifferent']), 'header/footer')
  if (typeof value.firstPageDifferent !== 'boolean') throw new Error('Invalid header or footer')
  for (const field of ['left', 'center', 'right'] as const) {
    if (typeof value[field] !== 'string' || value[field].length > 500) throw new Error('Invalid header or footer')
  }
}

export function validateDocumentSettings(value: DocumentSettings): void {
  if (!isRecord(value)) throw new Error('Invalid document settings')
  exactKeys(value, new Set([
    'pageSize', 'orientation', 'marginsInches', 'defaultFontFamily', 'defaultFontSizePt',
    'header', 'footer', 'showPageNumbers'
  ]), 'document setting')
  if (!['letter', 'a4'].includes(String(value.pageSize)) || value.orientation !== 'portrait') {
    throw new Error('Invalid document settings')
  }
  if (!FONTS.has(String(value.defaultFontFamily)) || !Number.isFinite(value.defaultFontSizePt)
    || value.defaultFontSizePt < 8 || value.defaultFontSizePt > 72) {
    throw new Error('Invalid document settings')
  }
  if (!isRecord(value.marginsInches)) throw new Error('Invalid page margin')
  exactKeys(value.marginsInches, new Set(['top', 'right', 'bottom', 'left']), 'margin')
  for (const side of ['top', 'right', 'bottom', 'left'] as const) {
    const margin = value.marginsInches[side]
    if (!Number.isFinite(margin) || margin < 0.25 || margin > 2
      || Math.abs(margin * 20 - Math.round(margin * 20)) > 1e-6) {
      throw new Error('Invalid page margin')
    }
  }
  validateHeaderFooter(value.header)
  validateHeaderFooter(value.footer)
  if (typeof value.showPageNumbers !== 'boolean') throw new Error('Invalid page number setting')
}

function validateSuggestion(value: unknown): void {
  if (!isRecord(value)) throw new Error('Invalid suggestion')
  exactKeys(value, new Set(['suggestionId', 'kind', 'author', 'createdAt']), 'suggestion')
  if (typeof value.suggestionId !== 'string' || !SUGGESTION_ID.test(value.suggestionId)
    || !['insert', 'delete'].includes(String(value.kind))
    || !['user', 'jobhunter'].includes(String(value.author))
    || !validTimestamp(value.createdAt)) {
    throw new Error('Invalid suggestion')
  }
}

function optionalFiniteRange(value: unknown, minimum: number, maximum: number, label: string): void {
  if (value === undefined || value === null) return
  if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum) {
    throw new Error(`Invalid ${label}`)
  }
}

function validateMark(mark: TiptapMarkJson): void {
  if (!isRecord(mark)) throw new Error('Invalid mark')
  exactKeys(mark, new Set(['type', 'attrs']), 'mark')
  if (typeof mark.type !== 'string' || !MARK_TYPES.has(mark.type)) throw new Error(`Unknown mark: ${String(mark.type)}`)
  if (mark.attrs !== undefined && !isRecord(mark.attrs)) throw new Error('Invalid mark attributes')
  const attrs = mark.attrs ?? {}
  if (['bold', 'italic', 'underline', 'strike'].includes(mark.type)) {
    exactKeys(attrs, new Set(), mark.type)
  } else if (mark.type === 'link') {
    exactKeys(attrs, new Set(['href', 'target', 'rel', 'class']), 'link')
    if (typeof attrs.href !== 'string' || attrs.href.length > 8_192 || !SAFE_LINK.test(attrs.href)) throw new Error('Unsafe link')
    if (attrs.target !== undefined && attrs.target !== null && attrs.target !== '_blank') throw new Error('Invalid link target')
    for (const field of ['rel', 'class'] as const) {
      if (attrs[field] !== undefined && attrs[field] !== null && typeof attrs[field] !== 'string') throw new Error('Invalid link attributes')
    }
  } else if (mark.type === 'suggestion') {
    validateSuggestion(attrs)
  } else if (mark.type === 'jobosField') {
    exactKeys(attrs, new Set(['fieldType', 'locked']), 'JobOS field')
    if (typeof attrs.fieldType !== 'string' || !attrs.fieldType || attrs.fieldType.length > 80
      || typeof attrs.locked !== 'boolean') throw new Error('Invalid JobOS field')
  } else if (mark.type === 'textStyle') {
    exactKeys(attrs, new Set(['fontFamily', 'fontSize', 'lineHeight', 'color', 'backgroundColor']), 'textStyle')
    if (attrs.fontFamily !== undefined && !FONTS.has(String(attrs.fontFamily))) throw new Error('Invalid font')
    const fontSize = typeof attrs.fontSize === 'string' && /^\d+(?:\.\d+)?pt$/.test(attrs.fontSize)
      ? Number(attrs.fontSize.slice(0, -2))
      : attrs.fontSize
    optionalFiniteRange(fontSize, 8, 72, 'font size')
    const lineHeight = typeof attrs.lineHeight === 'string' && /^\d+(?:\.\d+)?$/.test(attrs.lineHeight)
      ? Number(attrs.lineHeight)
      : attrs.lineHeight
    optionalFiniteRange(lineHeight, 0.8, 3, 'line height')
    for (const field of ['color', 'backgroundColor'] as const) {
      if (attrs[field] !== undefined && attrs[field] !== null
        && (typeof attrs[field] !== 'string' || !HEX_COLOR.test(attrs[field]))) throw new Error('Invalid text color')
    }
  }
}

function validateStructuralSuggestion(value: unknown): void {
  if (value === null) return
  if (!isRecord(value)) throw new Error('Invalid suggestion')
  exactKeys(value, new Set([
    'suggestionId', 'kind', 'author', 'createdAt', 'afterBlockId', 'semanticRole'
  ]), 'suggestion')
  if (typeof value.suggestionId !== 'string' || !SUGGESTION_ID.test(value.suggestionId)
    || !['insert', 'delete', 'move', 'set_role'].includes(String(value.kind))
    || !['user', 'jobhunter'].includes(String(value.author)) || !validTimestamp(value.createdAt)
    || (value.kind === 'move' && (typeof value.afterBlockId !== 'string' || !ID.test(value.afterBlockId)))
    || (value.kind === 'set_role' && !ROLES.has(value.semanticRole as SemanticRole))
    || (value.kind !== 'move' && value.afterBlockId !== undefined)
    || (value.kind !== 'set_role' && value.semanticRole !== undefined)) {
    throw new Error('Invalid suggestion')
  }
}

function validateBlockAttrs(node: TiptapNodeJson, attrs: Record<string, unknown>, seen: Set<string>): void {
  const blockId = attrs.jobosId
  if (typeof blockId !== 'string' || !ID.test(blockId)) throw new Error('Block requires a stable jobosId')
  if (seen.has(blockId)) throw new Error('Duplicate jobosId')
  seen.add(blockId)
  if (attrs.semanticRole !== null && !ROLES.has(attrs.semanticRole as SemanticRole)) throw new Error('Invalid semantic role')
  if (typeof attrs.locked !== 'boolean' || !ACTORS.has(String(attrs.origin))) throw new Error('Invalid block provenance')
  validateStructuralSuggestion(attrs.structuralSuggestion)
  if (node.type === 'jobosSection' && (typeof attrs.label !== 'string' || attrs.label.length > 120)) throw new Error('Invalid section label')
  if (node.type === 'heading' && ![1, 2, 3].includes(Number(attrs.level))) throw new Error('Invalid heading level')
  if (['paragraph', 'heading'].includes(node.type) && attrs.textAlign !== undefined
    && attrs.textAlign !== null && !['left', 'center', 'right', 'justify'].includes(String(attrs.textAlign))) {
    throw new Error('Invalid text alignment')
  }
}

function validateNodeAttrs(node: TiptapNodeJson, seen: Set<string>): void {
  if (node.attrs !== undefined && !isRecord(node.attrs)) throw new Error('Invalid node attributes')
  const attrs = node.attrs ?? {}
  exactKeys(attrs, ATTRIBUTE_KEYS[node.type] ?? new Set(), node.type)
  if (BLOCKS.has(node.type)) validateBlockAttrs(node, attrs, seen)
  if (node.type === 'orderedList' && attrs.start !== undefined
    && (!Number.isInteger(attrs.start) || Number(attrs.start) < 1 || Number(attrs.start) > 1_000_000)) {
    throw new Error('Invalid ordered-list start')
  }
  if (node.type === 'tableCell' || node.type === 'tableHeader') {
    for (const span of ['colspan', 'rowspan'] as const) {
      if (attrs[span] !== undefined && (!Number.isInteger(attrs[span]) || Number(attrs[span]) < 1 || Number(attrs[span]) > 20)) {
        throw new Error('Invalid table span')
      }
    }
    if (attrs.colwidth !== undefined && attrs.colwidth !== null
      && (!Array.isArray(attrs.colwidth) || attrs.colwidth.some(width => !Number.isFinite(width) || Number(width) <= 0))) {
      throw new Error('Invalid table column width')
    }
    if (attrs.backgroundColor !== undefined && attrs.backgroundColor !== null
      && (typeof attrs.backgroundColor !== 'string' || !HEX_COLOR.test(attrs.backgroundColor))) {
      throw new Error('Invalid table cell background color')
    }
    if (attrs.align !== undefined && attrs.align !== null
      && (typeof attrs.align !== 'string' || !['left', 'center', 'right', 'justify'].includes(attrs.align))) {
      throw new Error('Invalid table cell alignment')
    }
  }
}

function validateComments(comments: DocumentComment[], seenBlocks: Set<string>): void {
  if (!Array.isArray(comments) || comments.length > MAX_COMMENTS) throw new Error('Comment limit exceeded')
  const ids = new Set<string>()
  for (const comment of comments) {
    if (!isRecord(comment)) throw new Error('Invalid comment')
    exactKeys(comment, new Set(['commentId', 'blockId', 'author', 'body', 'createdAt', 'resolvedAt']), 'comment')
    if (typeof comment.commentId !== 'string' || !COMMENT_ID.test(comment.commentId) || ids.has(comment.commentId)) throw new Error('Invalid or duplicate comment ID')
    ids.add(comment.commentId)
    if (typeof comment.blockId !== 'string' || !seenBlocks.has(comment.blockId)) throw new Error('Comment target does not exist')
    if (!['user', 'jobhunter'].includes(String(comment.author)) || typeof comment.body !== 'string'
      || !comment.body || comment.body.length > MAX_COMMENT_CHARACTERS || !validTimestamp(comment.createdAt)
      || (comment.resolvedAt !== null && !validTimestamp(comment.resolvedAt))) {
      throw new Error('Invalid comment')
    }
  }
}

function validateImportReport(report: DocumentImportReport): void {
  if (!isRecord(report)) throw new Error('Invalid import report')
  exactKeys(report, new Set(['sourceFilename', 'importedAt', 'issues']), 'import report')
  if (report.sourceFilename !== null && (typeof report.sourceFilename !== 'string' || report.sourceFilename.length > 255)) throw new Error('Invalid import filename')
  if (report.importedAt !== null && !validTimestamp(report.importedAt)) throw new Error('Invalid import timestamp')
  if (!Array.isArray(report.issues) || report.issues.length > 200) throw new Error('Import issue limit exceeded')
  for (const issue of report.issues) {
    if (!isRecord(issue)) throw new Error('Invalid import issue')
    exactKeys(issue, new Set(['code', 'severity', 'message', 'count']), 'import issue')
    if (typeof issue.code !== 'string' || !issue.code || issue.code.length > 100
      || !['normalized', 'dropped'].includes(String(issue.severity))
      || typeof issue.message !== 'string' || !issue.message || issue.message.length > 500
      || !Number.isInteger(issue.count) || issue.count < 1 || issue.count > MAX_BLOCKS) {
      throw new Error('Invalid import issue')
    }
  }
}

function decodedBase64Bytes(value: string): number {
  const padding = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0
  return Math.floor(value.length * 3 / 4) - padding
}

function validateChildContent(node: TiptapNodeJson): void {
  const children = node.content ?? []
  for (const child of children) {
    if (!isRecord(child)) throw new Error('Invalid node')
    if (typeof child.type !== 'string' || !NODE_TYPES.has(child.type)) throw new Error(`Unknown node: ${String(child.type)}`)
  }
  const childTypes = children.map(child => child.type)
  const only = (allowed: Set<string>) => childTypes.every(type => allowed.has(type))
  if (node.type === 'doc' && (children.length === 0 || !childTypes.every(type => type === 'jobosSection'))) {
    throw new Error('Documents must contain one or more JobOS sections')
  }
  if ((node.type === 'jobosSection' || node.type === 'blockquote' || node.type === 'tableCell' || node.type === 'tableHeader')
    && (children.length === 0 || !only(BLOCK_CONTENT))) {
    throw new Error(`${node.type} must contain one or more block nodes`)
  }
  if ((node.type === 'paragraph' || node.type === 'heading') && !only(INLINE_CONTENT)) {
    throw new Error(`${node.type} may contain only inline content`)
  }
  if ((node.type === 'bulletList' || node.type === 'orderedList')
    && (children.length === 0 || !childTypes.every(type => type === 'listItem'))) {
    throw new Error(`${node.type} must contain one or more list items`)
  }
  if (node.type === 'listItem'
    && (children.length === 0 || childTypes[0] !== 'paragraph' || !childTypes.slice(1).every(type => BLOCK_CONTENT.has(type)))) {
    throw new Error('List items must begin with a paragraph and may contain blocks after it')
  }
  if (node.type === 'table' && (children.length === 0 || !childTypes.every(type => type === 'tableRow'))) {
    throw new Error('Tables must contain one or more rows')
  }
  if (node.type === 'tableRow' && !childTypes.every(type => type === 'tableCell' || type === 'tableHeader')) {
    throw new Error('Table rows may contain only cells')
  }
  if (['horizontalRule', 'hardBreak', 'pageBreak', 'image', 'text'].includes(node.type) && children.length > 0) {
    throw new Error(`${node.type} may not contain child nodes`)
  }
}

export function validateEditableContent(
  content: TiptapDocumentJson,
  settings = defaultDocumentSettings(),
  comments: DocumentComment[] = [],
  importReport: DocumentImportReport = emptyImportReport()
): void {
  validateDocumentSettings(settings)
  validateImportReport(importReport)
  if (!isRecord(content) || content.type !== 'doc') throw new Error('Document root must be doc')
  const bytes = new TextEncoder().encode(JSON.stringify({ content, settings, comments, importReport })).byteLength
  if (bytes > MAX_CANONICAL_BYTES) throw new Error('Document exceeds 8 MB')

  const seen = new Set<string>()
  let blocks = 0
  let images = 0
  const walk = (rawNode: TiptapNodeJson): void => {
    if (!isRecord(rawNode)) throw new Error('Invalid node')
    exactKeys(rawNode, TOP_LEVEL_KEYS, 'node')
    if (typeof rawNode.type !== 'string' || !NODE_TYPES.has(rawNode.type)) throw new Error(`Unknown node: ${String(rawNode.type)}`)
    if (rawNode.content !== undefined && !Array.isArray(rawNode.content)) throw new Error('Node content must be an array')
    if (rawNode.marks !== undefined && !Array.isArray(rawNode.marks)) throw new Error('Node marks must be an array')
    if (rawNode.type === 'text') {
      if (typeof rawNode.text !== 'string' || rawNode.content !== undefined || rawNode.attrs !== undefined) throw new Error('Invalid text node')
    } else if (rawNode.text !== undefined) {
      throw new Error('Only text nodes may contain text')
    }
    if (rawNode.type !== 'text' && rawNode.marks !== undefined) throw new Error('Only text nodes may contain marks')
    for (const mark of rawNode.marks ?? []) validateMark(mark)
    validateChildContent(rawNode)
    validateNodeAttrs(rawNode, seen)
    if (BLOCKS.has(rawNode.type)) blocks += 1
    if (rawNode.type === 'image') {
      images += 1
      const src = rawNode.attrs?.src
      if (typeof src !== 'string') throw new Error('Invalid image')
      const match = IMAGE.exec(src)
      if (!match || decodedBase64Bytes(match[2] ?? '') > MAX_IMAGE_BYTES) throw new Error('Unsafe or oversized image')
      for (const dimension of ['width', 'height'] as const) optionalFiniteRange(rawNode.attrs?.[dimension], 1, 20_000, 'image dimension')
      for (const field of ['alt', 'title'] as const) {
        const value = rawNode.attrs?.[field]
        if (value !== undefined && value !== null && (typeof value !== 'string' || value.length > 2_000)) throw new Error('Invalid image metadata')
      }
    }
    if (rawNode.type === 'table') {
      const rows = rawNode.content ?? []
      if (rows.length > MAX_TABLE_ROWS) throw new Error('Table row limit exceeded')
      for (const row of rows) {
        if (row.type !== 'tableRow') throw new Error('Tables may contain only rows')
        if ((row.content?.length ?? 0) > MAX_TABLE_COLUMNS) throw new Error('Table column limit exceeded')
      }
    }
    for (const child of rawNode.content ?? []) walk(child)
  }
  walk(content)
  if (blocks > MAX_BLOCKS) throw new Error('Block limit exceeded')
  if (images > MAX_IMAGES) throw new Error('Image limit exceeded')
  validateComments(comments, seen)
}

export function plainText(node: TiptapNodeJson): string {
  if (node.type === 'text') return node.text ?? ''
  if (node.type === 'hardBreak') return '\n'
  return (node.content ?? []).map(plainText).join('')
}

export function unresolvedSuggestionCount(content: TiptapDocumentJson): number {
  const ids = new Set<string>()
  const walk = (node: TiptapNodeJson): void => {
    const structural = node.attrs?.structuralSuggestion as { suggestionId?: unknown } | null | undefined
    if (typeof structural?.suggestionId === 'string') ids.add(structural.suggestionId)
    for (const mark of node.marks ?? []) {
      if (mark.type === 'suggestion' && typeof mark.attrs?.suggestionId === 'string') ids.add(mark.attrs.suggestionId)
    }
    for (const child of node.content ?? []) walk(child)
  }
  walk(content)
  return ids.size
}

export interface DocumentSuggestion {
  suggestionId: `sug_${string}`
  kind: 'insert' | 'delete' | 'move' | 'set_role'
  author: 'user' | 'jobhunter'
  blockId: `node_${string}` | null
  preview: string
  structural: boolean
}

export function collectDocumentSuggestions(content: TiptapDocumentJson): DocumentSuggestion[] {
  const suggestions = new Map<string, DocumentSuggestion>()
  const walk = (node: TiptapNodeJson, parentBlockId: `node_${string}` | null): void => {
    const nodeId = typeof node.attrs?.jobosId === 'string' ? node.attrs.jobosId as `node_${string}` : parentBlockId
    const structural = node.attrs?.structuralSuggestion as StructuralSuggestion | null | undefined
    if (structural && !suggestions.has(structural.suggestionId)) {
      suggestions.set(structural.suggestionId, {
        suggestionId: structural.suggestionId,
        kind: structural.kind,
        author: structural.author,
        blockId: nodeId,
        preview: plainText(node).slice(0, 180),
        structural: true
      })
    }
    for (const mark of node.marks ?? []) {
      const attrs = mark.attrs
      if (mark.type !== 'suggestion' || typeof attrs?.suggestionId !== 'string'
        || (attrs.kind !== 'insert' && attrs.kind !== 'delete') || suggestions.has(attrs.suggestionId)) continue
      suggestions.set(attrs.suggestionId, {
        suggestionId: attrs.suggestionId as `sug_${string}`,
        kind: attrs.kind,
        author: attrs.author as 'user' | 'jobhunter',
        blockId: nodeId,
        preview: (node.text ?? '').slice(0, 180),
        structural: false
      })
    }
    for (const child of node.content ?? []) walk(child, nodeId)
  }
  walk(content, null)
  return [...suggestions.values()]
}

export function resolveDocumentSuggestion(
  content: TiptapDocumentJson,
  suggestionId: `sug_${string}`,
  resolution: 'accept' | 'reject'
): TiptapDocumentJson {
  const resolveNode = (node: TiptapNodeJson): TiptapNodeJson | null => {
    const structural = node.attrs?.structuralSuggestion as StructuralSuggestion | null | undefined
    if (structural?.suggestionId === suggestionId) {
      const removeNode = (structural.kind === 'insert' && resolution === 'reject')
        || (structural.kind === 'delete' && resolution === 'accept')
      if (removeNode) return null
    }

    let removeText = false
    const marks = (node.marks ?? []).filter(mark => {
      if (mark.type !== 'suggestion' || mark.attrs?.suggestionId !== suggestionId) return true
      const kind = mark.attrs.kind
      removeText = (kind === 'insert' && resolution === 'reject')
        || (kind === 'delete' && resolution === 'accept')
      return false
    })
    if (removeText) return null

    const childResults = (node.content ?? []).map(child => ({
      proposal: child.attrs?.structuralSuggestion as StructuralSuggestion | null | undefined,
      resolved: resolveNode(child)
    }))
    let resolvedChildren = childResults
      .map(result => result.resolved)
      .filter((child): child is TiptapNodeJson => child !== null)
    if (resolution === 'accept') {
      const movingResult = childResults.find(result => (
        result.proposal?.suggestionId === suggestionId && result.proposal.kind === 'move'
      ))
      const moving = movingResult?.resolved
      const move = movingResult?.proposal
      if (moving && move?.afterBlockId) {
        resolvedChildren = resolvedChildren.filter(child => child !== moving)
        const destination = resolvedChildren.findIndex(child => child.attrs?.jobosId === move.afterBlockId)
        if (destination >= 0) resolvedChildren.splice(destination + 1, 0, moving)
      }
    }
    const attrs = structural?.suggestionId === suggestionId
      ? {
          ...node.attrs,
          ...(structural.kind === 'set_role' && resolution === 'accept'
            ? { semanticRole: structural.semanticRole }
            : {}),
          structuralSuggestion: null
        }
      : node.attrs
    return {
      ...node,
      ...(attrs === undefined ? {} : { attrs }),
      ...(node.content === undefined ? {} : { content: resolvedChildren }),
      ...(node.marks === undefined ? {} : { marks })
    }
  }

  const resolved = resolveNode(content)
  if (!resolved || resolved.type !== 'doc') throw new Error('Suggestion resolution cannot remove the document root')
  return resolved
}

/** Accept every proposal into a detached tree used only for deterministic current-state bytes. */
export function materializeDocumentCurrentState(content: TiptapDocumentJson): TiptapDocumentJson {
  return collectDocumentSuggestions(content).reduce(
    (current, suggestion) => resolveDocumentSuggestion(current, suggestion.suggestionId, 'accept'),
    content
  )
}

export function semanticOutline(content: TiptapDocumentJson): SemanticOutlineBlock[] {
  const result: SemanticOutlineBlock[] = []
  const walk = (node: TiptapNodeJson, parentSectionId: `node_${string}` | null): void => {
    const attrs = node.attrs
    const nodeId = typeof attrs?.jobosId === 'string' && ID.test(attrs.jobosId)
      ? attrs.jobosId as `node_${string}`
      : null
    const ownSection = node.type === 'jobosSection' && nodeId ? nodeId : parentSectionId
    if (BLOCKS.has(node.type) && nodeId) {
      result.push({
        blockId: nodeId,
        parentSectionId,
        nodeType: node.type,
        semanticRole: (attrs?.semanticRole as SemanticRole | null) ?? null,
        locked: attrs?.locked === true,
        text: plainText(node).slice(0, 2_000)
      })
    }
    for (const child of node.content ?? []) walk(child, ownSection)
  }
  walk(content, null)
  return result
}

export function stableSerialize(value: unknown): string {
  const sort = (item: unknown): unknown => Array.isArray(item)
    ? item.map(sort)
    : item && typeof item === 'object'
      ? Object.fromEntries(Object.entries(item as Record<string, unknown>)
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([key, child]) => [key, sort(child)]))
      : item
  return JSON.stringify(sort(value))
}
