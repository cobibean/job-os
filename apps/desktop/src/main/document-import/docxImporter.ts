import path from 'node:path'

import { generateJSON } from '@tiptap/html/server'
import { XMLParser } from 'fast-xml-parser'
import JSZip from 'jszip'
import mammoth from 'mammoth'

import type { DocumentImportIssue, DocumentImportReport, DocumentKey, DocumentSettings, TiptapDocumentJson } from '../../shared/editableDocuments.js'
import { MAX_IMAGE_BYTES } from '../../shared/editableDocumentSchema.js'
import { createDocumentExtensions } from '../../renderer/document-editor/extensions.js'
import { parseDocxMetadata } from './docxMetadataParser.js'
import { normalizeImportedDocument } from './normalizeImportedDocument.js'
import { sanitizeImportedHtml } from './sanitizeImportedHtml.js'

export const MAX_DOCX_BYTES = 20 * 1024 * 1024
export const MAX_ZIP_ENTRIES = 2_000
export const MAX_UNCOMPRESSED_BYTES = 60 * 1024 * 1024
const MAX_COMPRESSION_RATIO = 1_000
const REQUIRED = ['[Content_Types].xml', 'word/document.xml']
const XML_UNSAFE = /<!DOCTYPE|<!ENTITY/i

const STYLE_MAP = [
  "p[style-name='Title'] => h1:fresh",
  "p[style-name='Subtitle'] => p:fresh",
  "p[style-name='Heading 1'] => h1:fresh",
  "p[style-name='Heading 2'] => h2:fresh",
  "p[style-name='Heading 3'] => h3:fresh",
  "p[style-name='Quote'] => blockquote:fresh",
  "p[style-name='List Paragraph'] => ul > li:fresh",
  "p[style-name='Resume Heading'] => h2:fresh",
  "p[style-name='Resume Section'] => h2:fresh",
  "p[style-name='Contact Information'] => p:fresh"
]

export interface ImportedDocx {
  content: TiptapDocumentJson
  settings: DocumentSettings
  importReport: DocumentImportReport
  sanitizedHtml: string
}

interface CentralEntry { name: string; compressed: number; uncompressed: number; flags: number }

function centralEntries(bytes: Buffer): CentralEntry[] {
  let eocd = -1
  const minimum = Math.max(0, bytes.length - 65_557)
  for (let offset = bytes.length - 22; offset >= minimum; offset -= 1) {
    if (bytes.readUInt32LE(offset) === 0x06054b50) { eocd = offset; break }
  }
  if (eocd < 0) throw new Error('Malformed ZIP: end-of-central-directory record is missing')
  const count = bytes.readUInt16LE(eocd + 10)
  const size = bytes.readUInt32LE(eocd + 12)
  const start = bytes.readUInt32LE(eocd + 16)
  if (count === 0xffff || size === 0xffffffff || start === 0xffffffff) throw new Error('ZIP64 DOCX archives are not supported')
  if (count > MAX_ZIP_ENTRIES) throw new Error('DOCX ZIP entry limit exceeded')
  if (start + size > eocd) throw new Error('Malformed ZIP central directory')
  const entries: CentralEntry[] = []
  let offset = start
  for (let index = 0; index < count; index += 1) {
    if (offset + 46 > bytes.length || bytes.readUInt32LE(offset) !== 0x02014b50) throw new Error('Malformed ZIP central directory entry')
    const flags = bytes.readUInt16LE(offset + 8)
    const compressed = bytes.readUInt32LE(offset + 20)
    const uncompressed = bytes.readUInt32LE(offset + 24)
    const nameLength = bytes.readUInt16LE(offset + 28)
    const extraLength = bytes.readUInt16LE(offset + 30)
    const commentLength = bytes.readUInt16LE(offset + 32)
    const end = offset + 46 + nameLength + extraLength + commentLength
    if (end > bytes.length) throw new Error('Malformed ZIP central directory entry')
    const name = bytes.subarray(offset + 46, offset + 46 + nameLength).toString('utf8').replace(/\\/g, '/')
    entries.push({ name, compressed, uncompressed, flags })
    offset = end
  }
  if (offset !== start + size) throw new Error('Malformed ZIP central directory size')
  return entries
}

function verifyArchive(bytes: Buffer, filename: string): CentralEntry[] {
  if (bytes.length > MAX_DOCX_BYTES) throw new Error('DOCX exceeds the 20 MB input limit')
  if (bytes.length < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b || bytes[2] !== 0x03 || bytes[3] !== 0x04) throw new Error('File is not a ZIP-based DOCX')
  if (path.extname(filename).toLowerCase() !== '.docx') throw new Error('Only .docx files are supported; macro-enabled .docm files are rejected')
  const entries = centralEntries(bytes)
  const names = new Set<string>()
  let compressed = 0
  let uncompressed = 0
  for (const entry of entries) {
    const normalized = path.posix.normalize(entry.name)
    if (!entry.name || entry.name.startsWith('/') || normalized.startsWith('../') || normalized !== entry.name || names.has(entry.name)) throw new Error('DOCX contains an unsafe or duplicate ZIP entry name')
    names.add(entry.name)
    if ((entry.flags & 1) !== 0) throw new Error('Encrypted or password-protected DOCX files are not supported')
    compressed += entry.compressed
    uncompressed += entry.uncompressed
    if (uncompressed > MAX_UNCOMPRESSED_BYTES) throw new Error('DOCX ZIP uncompressed budget exceeded')
  }
  if (compressed > 0 && uncompressed / compressed > MAX_COMPRESSION_RATIO) throw new Error('DOCX ZIP compression ratio is unsafe')
  for (const required of REQUIRED) if (!names.has(required)) throw new Error(`DOCX is missing required entry: ${required}`)
  if (names.has('EncryptionInfo') || names.has('EncryptedPackage')) throw new Error('Encrypted or password-protected DOCX files are not supported')
  if ([...names].some(name => /vbaProject|scripts?/i.test(name))) throw new Error('Macro-enabled or scripted Word documents are not supported')
  return entries
}

function warning(code: string, severity: 'normalized' | 'dropped', message: string, count = 1): DocumentImportIssue {
  return { code, severity, message, count }
}

async function verifyRelationships(zip: JSZip, entries: CentralEntry[]): Promise<void> {
  const parser = new XMLParser({ ignoreAttributes: false })
  for (const entry of entries.filter(item => item.name.endsWith('.rels'))) {
    const source = await zip.file(entry.name)?.async('string')
    if (!source) continue
    const parsed = parser.parse(source) as Record<string, unknown>
    const relationships = (parsed.Relationships as Record<string, unknown> | undefined)?.Relationship
    const records = Array.isArray(relationships) ? relationships : relationships ? [relationships] : []
    for (const relation of records) {
      if (!relation || typeof relation !== 'object') continue
      const attributes = relation as Record<string, unknown>
      const target = String(attributes['@_Target'] ?? '')
      const targetMode = String(attributes['@_TargetMode'] ?? '')
      const type = String(attributes['@_Type'] ?? '')
      if (targetMode === 'External') {
        if (!type.endsWith('/hyperlink')) throw new Error('DOCX contains a disallowed external relationship')
        let protocol: string
        try {
          protocol = new URL(target).protocol
        } catch {
          throw new Error('DOCX contains an unsafe external hyperlink')
        }
        if (!['http:', 'https:', 'mailto:'].includes(protocol)) {
          throw new Error('DOCX contains an unsafe external hyperlink')
        }
        continue
      }
      const ownerDirectory = entry.name === '_rels/.rels'
        ? ''
        : path.posix.dirname(path.posix.dirname(entry.name))
      const resolved = path.posix.normalize(path.posix.join(ownerDirectory, target.replace(/\\/g, '/')))
      if (!target || target.startsWith('/') || resolved.startsWith('../')) {
        throw new Error('DOCX contains an unsafe internal relationship target')
      }
    }
  }
}

export async function importDocx(bytesLike: Uint8Array, sourceFilename: string, documentKey: DocumentKey, now = new Date()): Promise<ImportedDocx> {
  const bytes = Buffer.from(bytesLike)
  const filename = path.basename(sourceFilename).slice(0, 255)
  const entries = verifyArchive(bytes, filename)
  let zip: JSZip
  try {
    zip = await JSZip.loadAsync(bytes, { checkCRC32: true, createFolders: false })
  } catch {
    throw new Error('Malformed DOCX ZIP archive')
  }
  for (const entry of entries) {
    if (!/\.(?:xml|rels)$/i.test(entry.name)) continue
    const value = await zip.file(entry.name)?.async('string')
    if (value && XML_UNSAFE.test(value)) throw new Error(`Unsafe XML declaration in ${entry.name}`)
  }
  await verifyRelationships(zip, entries)
  const contentTypes = await zip.file('[Content_Types].xml')?.async('string')
  if (!contentTypes || /macroEnabled|vbaProject|application\/vnd\.ms-word/i.test(contentTypes)) throw new Error('Macro-enabled .docm content is not supported')
  if (!/application\/vnd\.openxmlformats-officedocument\.wordprocessingml\.document\.main\+xml/i.test(contentTypes)) {
    throw new Error('ZIP archive is not a valid DOCX package')
  }

  const metadata = await parseDocxMetadata(zip)
  const issues = [...metadata.issues]
  let imageCount = 0
  const converted = await mammoth.convertToHtml(
    { buffer: bytes },
    {
      styleMap: STYLE_MAP,
      includeDefaultStyleMap: true,
      convertImage: mammoth.images.imgElement(async image => {
        const contentType = image.contentType.toLowerCase()
        if (!['image/png', 'image/jpeg', 'image/gif'].includes(contentType)) {
          issues.push(warning('unsupported_image_dropped', 'dropped', `An unsupported ${contentType || 'unknown'} image was dropped.`))
          return { src: '' }
        }
        const base64 = await image.read('base64')
        const imageBytes = Buffer.from(base64, 'base64').byteLength
        imageCount += 1
        if (imageCount > 20 || imageBytes > MAX_IMAGE_BYTES) {
          issues.push(warning('oversized_image_dropped', 'dropped', 'An image exceeding the import limits was dropped.'))
          return { src: '' }
        }
        return { src: `data:${contentType};base64,${base64}` }
      })
    }
  )
  for (const message of converted.messages) issues.push(warning('mammoth_conversion_warning', 'normalized', String(message.message).slice(0, 500)))
  const sanitizedHtml = sanitizeImportedHtml(converted.value)
  const parseHtml = `<section data-jobos-section>${sanitizedHtml}</section>`
  const parsed = generateJSON(parseHtml, createDocumentExtensions()) as TiptapDocumentJson
  const wrapper = parsed.content?.[0]
  const raw: TiptapDocumentJson = {
    type: 'doc',
    content: wrapper?.type === 'jobosSection' ? wrapper.content ?? [] : parsed.content ?? []
  }
  const importedAt = now.toISOString()
  const content = normalizeImportedDocument(raw, {
    documentKey,
    explicitPageBreakAfterParagraphs: metadata.explicitPageBreakAfterParagraphs,
    issues,
    sourceFilename: filename,
    importedAt
  })
  return {
    content,
    settings: metadata.settings,
    importReport: { sourceFilename: filename, importedAt, issues },
    sanitizedHtml
  }
}
