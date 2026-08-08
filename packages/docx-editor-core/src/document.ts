import {
  buildBlankDocx,
  findChartWorkbookPath,
  parseChartPartXml,
  parseDocx,
  patchChartPartXml,
  patchChartWorkbookXlsxBase64,
  readDocxPartBase64,
  saveDocx,
  type ParsedDocFull,
} from '@jobos/docx-engine'

import { docStyleCss } from './doc-style-css.js'
import { blocksToPmDoc, pmDocToSavePlan, type PmNode } from './editor/convert.js'
import { editorExtensions } from './editor/extensions.js'
import { setModuleLang, type Lang } from './locale.js'

export interface DocumentCapabilities {
  mode: 'editable' | 'protected'
  editableBlockCount: number
  protectedBlockCount: number
  hasTables: boolean
  hasImages: boolean
  hasTextboxes: boolean
  hasTrackedChanges: boolean
  warnings: string[]
}

export interface EditingDocument {
  parsed: ParsedDocFull
  pmDoc: PmNode
  styleCss: string
  capabilities: DocumentCapabilities
}

export async function createBlankDocx(): Promise<Uint8Array> {
  return buildBlankDocx()
}

export async function parseDocxForEditing(bytes: Uint8Array, language: Lang = 'en'): Promise<EditingDocument> {
  setModuleLang(language)
  const parsed = await parseDocx(bytes)
  return {
    parsed,
    pmDoc: blocksToPmDoc(parsed.blocks),
    styleCss: docStyleCss(parsed),
    capabilities: inspectDocumentCapabilities(parsed),
  }
}

export function createEditorExtensions() {
  return [...editorExtensions]
}

export function inspectDocumentCapabilities(parsed: ParsedDocFull): DocumentCapabilities {
  const visible = parsed.blocks.filter(block => !block.hidden)
  const protectedBlocks = visible.filter(block => block.type === 'passthrough' || block.type === 'image')
  const warnings: string[] = []
  if (protectedBlocks.length > 0) warnings.push(`${protectedBlocks.length} complex block(s) are protected and preserved as whole units.`)
  if (parsed.protection) warnings.push('This DOCX contains Word editing restrictions; JobOS preserves the protection metadata.')
  return {
    mode: protectedBlocks.length > 0 || Boolean(parsed.protection) ? 'protected' : 'editable',
    editableBlockCount: visible.length - protectedBlocks.length,
    protectedBlockCount: protectedBlocks.length,
    hasTables: visible.some(block => block.type === 'table'),
    hasImages: visible.some(block => block.type === 'image'),
    hasTextboxes: visible.some(block => Boolean(block.textboxes?.length)),
    hasTrackedChanges: visible.some(block => Boolean(block.blockRevision || block.moveRevision || block.pPrChangeInfo)),
    warnings,
  }
}

export async function buildPatchedDocx(document: EditingDocument, pmDoc: PmNode, savedAt = new Date().toISOString()): Promise<Uint8Array> {
  const plan = pmDocToSavePlan(pmDoc, document.parsed.blocks)
  const partXml: Record<string, string> = {}
  const partBinary: Record<string, string> = {}
  for (const { partPath, patch } of plan.chartPatches) {
    const originalPart = document.parsed.extras.chartParts[partPath]
    if (!originalPart) continue
    const patchedXml = patchChartPartXml(originalPart, patch)
    partXml[partPath] = patchedXml
    const workbookPath = await findChartWorkbookPath(document.parsed.internal.originalBytes, partPath)
    if (!workbookPath) continue
    const existingBase64 = await readDocxPartBase64(document.parsed.internal.originalBytes, workbookPath)
    const display = parseChartPartXml(patchedXml, partPath)
    if (!existingBase64 || !display) continue
    const series = display.series.map((item, index) => ({ name: item.name ?? `Series${index + 1}`, values: item.values as (number | null)[] }))
    const updated = await patchChartWorkbookXlsxBase64(existingBase64, display.categories, series)
    if (updated) partBinary[workbookPath] = updated
  }
  return saveDocx(document.parsed, plan.saveBlocks, {
    savedAt,
    partXml: Object.keys(partXml).length ? partXml : undefined,
    partBinary: Object.keys(partBinary).length ? partBinary : undefined,
  })
}
