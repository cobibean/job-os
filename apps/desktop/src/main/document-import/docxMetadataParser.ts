import JSZip from 'jszip'
import { XMLParser } from 'fast-xml-parser'

import type { DocumentImportIssue, DocumentSettings } from '../../shared/editableDocuments.js'
import { defaultDocumentSettings } from '../../shared/editableDocumentSchema.js'

const XML_DECLARATION = /<!DOCTYPE|<!ENTITY/i
const parser = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: '@_', processEntities: false, parseTagValue: false })

export interface DocxMetadata {
  settings: DocumentSettings
  explicitPageBreakAfterParagraphs: number[]
  issues: DocumentImportIssue[]
}

function array<T>(value: T | T[] | undefined): T[] {
  return value === undefined ? [] : Array.isArray(value) ? value : [value]
}

function attr(node: unknown, name: string): string | undefined {
  if (!node || typeof node !== 'object') return undefined
  const value = (node as Record<string, unknown>)[`@_w:${name}`] ?? (node as Record<string, unknown>)[`@_${name}`]
  return typeof value === 'string' || typeof value === 'number' ? String(value) : undefined
}

async function xml(zip: JSZip, path: string, required = false): Promise<Record<string, unknown> | null> {
  const entry = zip.file(path)
  if (!entry) {
    if (required) throw new Error(`DOCX is missing required entry: ${path}`)
    return null
  }
  const text = await entry.async('string')
  if (XML_DECLARATION.test(text)) throw new Error(`Unsafe XML declaration in ${path}`)
  try {
    return parser.parse(text) as Record<string, unknown>
  } catch {
    throw new Error(`Malformed XML in ${path}`)
  }
}

function textFromNode(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map(textFromNode).join('')
  if (!value || typeof value !== 'object') return ''
  const record = value as Record<string, unknown>
  const own = record['#text']
  return (typeof own === 'string' || typeof own === 'number' ? String(own) : '')
    + Object.entries(record).filter(([key]) => !key.startsWith('@_') && key !== '#text').map(([, child]) => textFromNode(child)).join('')
}

function issue(code: string, severity: 'normalized' | 'dropped', message: string, count: number): DocumentImportIssue {
  return { code, severity, message, count }
}

function countMatches(text: string, expression: RegExp): number {
  return text.match(expression)?.length ?? 0
}

function roundedMargin(value: string | undefined, fallback: number): number {
  if (!value) return fallback
  const inches = Number(value) / 1440
  if (!Number.isFinite(inches)) return fallback
  return Math.min(2, Math.max(0.25, Math.round(inches * 20) / 20))
}

async function relationships(zip: JSZip): Promise<Map<string, string>> {
  const root = await xml(zip, 'word/_rels/document.xml.rels')
  const result = new Map<string, string>()
  const rels = (root?.Relationships as Record<string, unknown> | undefined)?.Relationship
  for (const relation of array(rels)) {
    if (!relation || typeof relation !== 'object') continue
    const record = relation as Record<string, unknown>
    const id = String(record['@_Id'] ?? '')
    const target = String(record['@_Target'] ?? '')
    const mode = String(record['@_TargetMode'] ?? '')
    const type = String(record['@_Type'] ?? '')
    if (mode === 'External' && !type.endsWith('/hyperlink')) throw new Error('DOCX contains a disallowed external relationship')
    if (id && target && mode !== 'External' && !target.includes('..')) result.set(id, `word/${target.replace(/^\//, '')}`)
  }
  return result
}

async function headerFooterText(zip: JSZip, path: string | undefined): Promise<string> {
  if (!path) return ''
  const root = await xml(zip, path)
  return textFromNode(root).replace(/\s+/g, ' ').trim().slice(0, 500)
}

export async function parseDocxMetadata(zip: JSZip): Promise<DocxMetadata> {
  const documentEntry = zip.file('word/document.xml')
  if (!documentEntry) throw new Error('DOCX is missing required entry: word/document.xml')
  const documentText = await documentEntry.async('string')
  if (XML_DECLARATION.test(documentText)) throw new Error('Unsafe XML declaration in word/document.xml')
  const document = await xml(zip, 'word/document.xml', true)
  const body = ((document?.['w:document'] ?? document?.document) as Record<string, unknown> | undefined)?.['w:body'] as Record<string, unknown> | undefined
    ?? ((document?.['w:document'] ?? document?.document) as Record<string, unknown> | undefined)?.body as Record<string, unknown> | undefined
  if (!body) throw new Error('Malformed Word document body')

  const paragraphs = array((body['w:p'] ?? body.p) as unknown)
  const explicitPageBreakAfterParagraphs: number[] = []
  paragraphs.forEach((paragraph, index) => {
    const raw = JSON.stringify(paragraph)
    if (/"@_w:type":"page"|"@_type":"page"|w:pageBreakBefore|"pageBreakBefore"/.test(raw)) explicitPageBreakAfterParagraphs.push(index)
  })

  const sections = array((body['w:sectPr'] ?? body.sectPr) as unknown)
  for (const paragraph of paragraphs) {
    if (paragraph && typeof paragraph === 'object') {
      const pPr = (paragraph as Record<string, unknown>)['w:pPr'] ?? (paragraph as Record<string, unknown>).pPr
      if (pPr && typeof pPr === 'object') {
        const section = (pPr as Record<string, unknown>)['w:sectPr'] ?? (pPr as Record<string, unknown>).sectPr
        if (section) sections.push(section as never)
      }
    }
  }
  const firstSection = (sections[0] && typeof sections[0] === 'object' ? sections[0] : {}) as Record<string, unknown>
  const pageSize = (firstSection['w:pgSz'] ?? firstSection.pgSz) as Record<string, unknown> | undefined
  const pageMargins = (firstSection['w:pgMar'] ?? firstSection.pgMar) as Record<string, unknown> | undefined
  const width = Number(attr(pageSize, 'w'))
  const height = Number(attr(pageSize, 'h'))
  const isA4 = Number.isFinite(width) && Number.isFinite(height) && Math.abs(width - 11906) < 400 && Math.abs(height - 16838) < 400
  const defaults = defaultDocumentSettings()
  const rels = await relationships(zip)
  const headerReference = array((firstSection['w:headerReference'] ?? firstSection.headerReference) as unknown)[0]
  const footerReference = array((firstSection['w:footerReference'] ?? firstSection.footerReference) as unknown)[0]
  const headerId = headerReference && typeof headerReference === 'object' ? String((headerReference as Record<string, unknown>)['@_r:id'] ?? '') : ''
  const footerId = footerReference && typeof footerReference === 'object' ? String((footerReference as Record<string, unknown>)['@_r:id'] ?? '') : ''
  const header = await headerFooterText(zip, rels.get(headerId))
  const footer = await headerFooterText(zip, rels.get(footerId))

  const issues: DocumentImportIssue[] = []
  const features: Array<[string, RegExp, 'normalized' | 'dropped', string]> = [
    ['tracked_changes_flattened', /<w:(ins|del)\b/g, 'normalized', 'Tracked changes were flattened to current visible text.'],
    ['comments_dropped', /<w:comment(?:Reference|RangeStart|RangeEnd)\b/g, 'dropped', 'Word comments were removed.'],
    ['floating_images_flattened', /<wp:anchor\b/g, 'normalized', 'Floating images were converted to inline content.'],
    ['unsupported_objects_dropped', /<w:(object|pict)\b|<o:OLEObject\b/g, 'dropped', 'Embedded objects or legacy shapes were dropped.'],
    ['equations_dropped', /<m:oMath\b|<m:oMathPara\b/g, 'dropped', 'Equations were dropped.'],
    ['fields_flattened', /<w:fldChar\b|<w:instrText\b/g, 'normalized', 'Word fields were flattened to displayed text.']
  ]
  for (const [code, expression, severity, message] of features) {
    const count = countMatches(documentText, expression)
    if (count) issues.push(issue(code, severity, message, count))
  }
  const names = Object.keys(zip.files)
  const embeddedParts = names.filter(name => /^word\/(?:embeddings|activeX)\//i.test(name)).length
  if (embeddedParts && !issues.some(value => value.code === 'unsupported_objects_dropped')) {
    issues.push(issue('unsupported_objects_dropped', 'dropped', 'Embedded objects or ActiveX content were dropped.', embeddedParts))
  }
  const commentPart = names.find(name => /^word\/comments(?:Extended)?\.xml$/i.test(name))
  if (commentPart && !issues.some(value => value.code === 'comments_dropped')) {
    const commentsText = await zip.file(commentPart)?.async('string')
    const count = countMatches(commentsText ?? '', /<w:comment\b/g)
    if (count) issues.push(issue('comments_dropped', 'dropped', 'Word comments were removed.', count))
  }
  const columns = countMatches(documentText, /<w:cols\b/g)
  if (columns) issues.push(issue('columns_flattened', 'normalized', 'Word columns were flattened to single-column reading order.', columns))
  const textBoxes = countMatches(documentText, /<w:txbxContent\b/g)
  if (textBoxes) issues.push(issue('text_boxes_flattened', 'normalized', 'Text boxes were flattened into reading-order paragraphs.', textBoxes))
  if (sections.length > 1) issues.push(issue('multiple_sections_flattened', 'normalized', 'Multiple Word sections use the first section page settings.', sections.length))

  return {
    settings: {
      ...defaults,
      pageSize: isA4 ? 'a4' : 'letter',
      marginsInches: {
        top: roundedMargin(attr(pageMargins, 'top'), defaults.marginsInches.top),
        right: roundedMargin(attr(pageMargins, 'right'), defaults.marginsInches.right),
        bottom: roundedMargin(attr(pageMargins, 'bottom'), defaults.marginsInches.bottom),
        left: roundedMargin(attr(pageMargins, 'left'), defaults.marginsInches.left)
      },
      header: { ...defaults.header, center: header },
      footer: { ...defaults.footer, center: footer },
      showPageNumbers: /PAGE|NUMPAGES/i.test(`${header} ${footer}`)
    },
    explicitPageBreakAfterParagraphs,
    issues
  }
}
