import {
  AlignmentType,
  Document,
  ExternalHyperlink,
  Footer,
  Header,
  HeadingLevel,
  ImageRun,
  Packer,
  PageBreak,
  PageNumber,
  Paragraph,
  Table,
  TableCell,
  TableRow,
  TextRun,
  WidthType,
  type FileChild,
  type ParagraphChild
} from 'docx'

import type { DocumentSettings, EditableDocument, TiptapMarkJson, TiptapNodeJson } from '../../shared/editableDocuments.js'
import { materializeDocumentCurrentState, unresolvedSuggestionCount, validateEditableContent } from '../../shared/editableDocumentSchema.js'

const SAFE_LINK = /^(https?:|mailto:)/i
const IMAGE = /^data:image\/(png|jpeg|gif);base64,([A-Za-z0-9+/]*={0,2})$/

function alignment(value: unknown) {
  if (value === 'center') return AlignmentType.CENTER
  if (value === 'right') return AlignmentType.RIGHT
  if (value === 'justify') return AlignmentType.JUSTIFIED
  return AlignmentType.LEFT
}

function runOptions(text: string, marks: TiptapMarkJson[]) {
  const options: {
    text: string
    bold?: boolean
    italics?: boolean
    underline?: Record<string, never>
    strike?: boolean
    font?: string
    color?: string
    size?: number
    shading?: { fill: string }
  } = { text }
  for (const mark of marks) {
    const attrs = mark.attrs ?? {}
    if (mark.type === 'bold') options.bold = true
    if (mark.type === 'italic') options.italics = true
    if (mark.type === 'underline') options.underline = {}
    if (mark.type === 'strike') options.strike = true
    if (mark.type === 'code') options.font = 'Courier New'
    if (mark.type === 'textStyle') {
      if (typeof attrs.color === 'string' && /^#[0-9a-f]{6}$/i.test(attrs.color)) options.color = attrs.color.slice(1)
      if (typeof attrs.backgroundColor === 'string' && /^#[0-9a-f]{6}$/i.test(attrs.backgroundColor)) options.shading = { fill: attrs.backgroundColor.slice(1) }
      if (typeof attrs.fontFamily === 'string' && /^[A-Za-z ]{1,40}$/.test(attrs.fontFamily)) options.font = attrs.fontFamily
      if (typeof attrs.fontSize === 'string' && /^([8-9]|[1-6][0-9]|7[0-2])pt$/.test(attrs.fontSize)) options.size = Number(attrs.fontSize.slice(0, -2)) * 2
    }
  }
  return options
}

function paragraphChildren(node: TiptapNodeJson): ParagraphChild[] {
  const result: ParagraphChild[] = []
  for (const child of node.content ?? []) {
    if (child.type === 'text') {
      const marks = child.marks ?? []
      const link = marks.find(mark => mark.type === 'link')
      const run = new TextRun(runOptions(child.text ?? '', marks.filter(mark => mark !== link)))
      const href = link?.attrs?.href
      result.push(typeof href === 'string' && SAFE_LINK.test(href)
        ? new ExternalHyperlink({ link: href, children: [run] })
        : run)
    } else if (child.type === 'hardBreak') {
      result.push(new TextRun({ break: 1 }))
    } else if (child.type === 'image') {
      const src = typeof child.attrs?.src === 'string' ? child.attrs.src : ''
      const matched = IMAGE.exec(src)
      if (!matched) continue
      const imageType = matched[1] === 'jpeg' ? 'jpg' : matched[1] as 'png' | 'gif'
      result.push(new ImageRun({
        type: imageType,
        data: Uint8Array.from(Buffer.from(matched[2]!, 'base64')),
        transformation: {
          width: Math.min(1200, Math.max(1, Number(child.attrs?.width) || 600)),
          height: Math.min(1200, Math.max(1, Number(child.attrs?.height) || 400))
        },
        altText: {
          title: typeof child.attrs?.title === 'string' ? child.attrs.title : '',
          description: typeof child.attrs?.alt === 'string' ? child.attrs.alt : '',
          name: 'JobOS document image'
        }
      }))
    }
  }
  return result
}

function paragraph(node: TiptapNodeJson, options: { bullet?: boolean; numberingReference?: string; level?: number } = {}): Paragraph {
  const level = Number(node.attrs?.level)
  const lineHeight = (node.content ?? []).flatMap(child => child.marks ?? [])
    .find(mark => mark.type === 'textStyle')?.attrs?.lineHeight
  const line = typeof lineHeight === 'string' && /^\d+(?:\.\d+)?$/.test(lineHeight)
    ? Math.round(Number(lineHeight) * 240)
    : undefined
  return new Paragraph({
    children: paragraphChildren(node),
    alignment: alignment(node.attrs?.textAlign),
    ...(node.type === 'heading' ? {
      heading: level === 1 ? HeadingLevel.HEADING_1 : level === 3 ? HeadingLevel.HEADING_3 : HeadingLevel.HEADING_2
    } : {}),
    ...(node.type === 'blockquote' ? { indent: { left: 540 }, border: { left: { color: 'B5BAC2', size: 10, style: 'single', space: 8 } } } : {}),
    ...(options.bullet ? { bullet: { level: options.level ?? 0 } } : {}),
    ...(options.numberingReference ? { numbering: { reference: options.numberingReference, level: options.level ?? 0 } } : {}),
    spacing: { after: node.type === 'heading' ? 120 : 100, ...(line ? { line } : {}) }
  })
}

function tableCell(node: TiptapNodeJson, orderedReferences: WeakMap<object, string>): TableCell {
  const children = blocks(node, orderedReferences)
  return new TableCell({
    children: children.length ? children : [new Paragraph('')],
    columnSpan: Math.max(1, Number(node.attrs?.colspan) || 1),
    rowSpan: Math.max(1, Number(node.attrs?.rowspan) || 1),
    ...(typeof node.attrs?.backgroundColor === 'string' && /^#[0-9a-f]{6}$/i.test(node.attrs.backgroundColor)
      ? { shading: { fill: node.attrs.backgroundColor.slice(1) } }
      : {})
  })
}

function blocks(node: TiptapNodeJson, orderedReferences: WeakMap<object, string>, listLevel = 0): FileChild[] {
  if (['doc', 'jobosSection', 'tableCell', 'tableHeader', 'listItem'].includes(node.type)) {
    return (node.content ?? []).flatMap(child => blocks(child, orderedReferences, listLevel))
  }
  if (['paragraph', 'heading', 'blockquote'].includes(node.type)) return [paragraph(node)]
  if (node.type === 'horizontalRule') return [new Paragraph({ border: { bottom: { color: '8B929D', size: 6, style: 'single', space: 2 } } })]
  if (node.type === 'pageBreak') return [new Paragraph({ children: [new PageBreak()] })]
  if (node.type === 'bulletList' || node.type === 'orderedList') {
    const ordered = node.type === 'orderedList'
    return (node.content ?? []).flatMap(item => {
      const direct = (item.content ?? []).filter(child => ['paragraph', 'heading', 'blockquote'].includes(child.type))
      const nested = (item.content ?? []).filter(child => child.type === 'bulletList' || child.type === 'orderedList')
      return [
        ...direct.map(child => paragraph(child, {
          bullet: !ordered,
          numberingReference: ordered ? orderedReferences.get(node) : undefined,
          level: Math.min(8, listLevel)
        })),
        ...nested.flatMap(child => blocks(child, orderedReferences, listLevel + 1))
      ]
    })
  }
  if (node.type === 'table') {
    return [new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      rows: (node.content ?? []).filter(row => row.type === 'tableRow').map(row => new TableRow({
        tableHeader: (row.content ?? []).some(cell => cell.type === 'tableHeader'),
        children: (row.content ?? []).filter(cell => cell.type === 'tableCell' || cell.type === 'tableHeader').map(cell => tableCell(cell, orderedReferences))
      }))
    })]
  }
  if (node.type === 'image') return [new Paragraph({ children: paragraphChildren({ type: 'paragraph', content: [node] }) })]
  return []
}

function pageTokenRuns(value: string): TextRun[] {
  const parts = value.split(/(\{page\}|\{pages\})/g)
  return parts.flatMap(part => part === '{page}' ? [new TextRun({ children: [PageNumber.CURRENT] })]
    : part === '{pages}' ? [new TextRun({ children: [PageNumber.TOTAL_PAGES] })]
      : part ? [new TextRun({ text: part, size: 18, color: '5F6670' })] : [])
}

function headerFooterParagraphs(values: string[], includePageNumber = false): Paragraph[] {
  const alignments = [AlignmentType.LEFT, AlignmentType.CENTER, AlignmentType.RIGHT]
  return values.map((value, index) => new Paragraph({
    alignment: alignments[index],
    children: [
      ...pageTokenRuns(value),
      ...(includePageNumber && index === 1 && !value.includes('{page}')
        ? [new TextRun({ children: [value ? ' · Page ' : 'Page ', PageNumber.CURRENT] })]
        : [])
    ]
  }))
}

function sectionPage(settings: DocumentSettings) {
  const dimensions = settings.pageSize === 'a4' ? { width: 11906, height: 16838 } : { width: 12240, height: 15840 }
  const twips = (inches: number) => Math.round(inches * 1440)
  return {
    size: dimensions,
    margin: {
      top: twips(settings.marginsInches.top),
      right: twips(settings.marginsInches.right),
      bottom: twips(settings.marginsInches.bottom),
      left: twips(settings.marginsInches.left),
      header: 360,
      footer: 360,
      gutter: 0
    }
  }
}

function orderedListNumbering(content: TiptapNodeJson) {
  const references = new WeakMap<object, string>()
  const config: Array<{
    reference: string
    levels: Array<{
      level: number
      format: 'decimal'
      text: string
      alignment: typeof AlignmentType.START
      start: number
      style: { paragraph: { indent: { left: number; hanging: number } } }
    }>
  }> = []
  const visit = (node: TiptapNodeJson) => {
    if (node.type === 'orderedList') {
      const reference = `jobos-numbering-${config.length + 1}`
      const start = Math.max(1, Number(node.attrs?.start) || 1)
      references.set(node, reference)
      config.push({
        reference,
        levels: Array.from({ length: 9 }, (_, level) => ({
          level,
          format: 'decimal' as const,
          text: `%${level + 1}.`,
          alignment: AlignmentType.START,
          start,
          style: { paragraph: { indent: { left: 720 * (level + 1), hanging: 360 } } }
        }))
      })
    }
    for (const child of node.content ?? []) visit(child)
  }
  visit(content)
  return { references, config }
}

export async function exportEditableDocumentDocx(
  document: EditableDocument,
  options: { allowUnresolvedSuggestions?: boolean } = {}
): Promise<Uint8Array> {
  validateEditableContent(document.content, document.settings, document.comments)
  if (!options.allowUnresolvedSuggestions && unresolvedSuggestionCount(document.content) > 0) throw new Error('Resolve every suggestion before preview or export')
  const content = options.allowUnresolvedSuggestions
    ? materializeDocumentCurrentState(document.content)
    : document.content
  const settings = document.settings
  const numbering = orderedListNumbering(content)
  const titlePage = settings.header.firstPageDifferent || settings.footer.firstPageDifferent
  const wordDocument = new Document({
    creator: 'JobOS',
    title: document.documentLabel,
    description: `JobOS ${document.documentLabel} revision ${document.revision}`,
    numbering: { config: numbering.config },
    sections: [{
      properties: { page: sectionPage(settings), titlePage },
      headers: {
        default: new Header({ children: headerFooterParagraphs([settings.header.left, settings.header.center, settings.header.right]) }),
        ...(titlePage ? { first: new Header({ children: [new Paragraph('')] }) } : {})
      },
      footers: {
        default: new Footer({ children: headerFooterParagraphs([settings.footer.left, settings.footer.center, settings.footer.right], settings.showPageNumbers) }),
        ...(titlePage ? { first: new Footer({ children: [new Paragraph('')] }) } : {})
      },
      children: blocks(content, numbering.references)
    }]
  })
  return Uint8Array.from(await Packer.toBuffer(wordDocument))
}
