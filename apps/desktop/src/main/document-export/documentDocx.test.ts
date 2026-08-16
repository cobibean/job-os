// @vitest-environment node

import JSZip from 'jszip'
import { describe, expect, it } from 'vitest'

import type { EditableDocument } from '../../shared/editableDocuments.js'
import { createBlankDocument, defaultDocumentSettings, plainText } from '../../shared/editableDocumentSchema.js'
import { importDocx } from '../document-import/docxImporter.js'
import { exportEditableDocumentDocx } from './documentDocx.js'

function blockAttrs() {
  return { jobosId: `node_${crypto.randomUUID()}`, semanticRole: 'summary', locked: false, origin: 'user', structuralSuggestion: null }
}

function documentFixture(): EditableDocument {
  const content = createBlankDocument('resume')
  const summary = content.content?.[1]?.content?.[0]
  if (!summary) throw new Error('Fixture summary missing')
  summary.content = [
    { type: 'text', text: 'Product ', marks: [{ type: 'bold' }] },
    { type: 'text', text: 'leader', marks: [{ type: 'link', attrs: { href: 'https://example.com/profile' } }] }
  ]
  content.content?.[1]?.content?.push({
    type: 'pageBreak',
    attrs: { jobosId: `node_${crypto.randomUUID()}`, semanticRole: null, locked: false, origin: 'user', structuralSuggestion: null }
  })
  const settings = defaultDocumentSettings()
  settings.pageSize = 'a4'
  settings.marginsInches = { top: 0.5, right: 0.75, bottom: 1, left: 1.25 }
  settings.header.left = 'Example User'
  settings.footer.right = 'Draft'
  settings.showPageNumbers = true
  return {
    schemaVersion: 1,
    documentId: 'edoc_ABCDEFGHIJKLMNOPQRSTUVWX',
    jobId: 'job-7',
    documentKey: 'resume',
    documentLabel: 'Resume',
    revision: 4,
    content,
    settings,
    comments: [],
    sourceArtifactId: null,
    sourceFilename: null,
    sourceSha256: null,
    publishedRevision: null,
    importReport: { sourceFilename: null, importedAt: null, issues: [] },
    createdAt: '2026-08-07T00:00:00Z',
    updatedAt: '2026-08-07T00:00:00Z'
  }
}

describe('native DOCX export', () => {
  it('writes a real Word ZIP with canonical content, page geometry, headers, links, and page breaks', async () => {
    const bytes = await exportEditableDocumentDocx(documentFixture())
    expect(Array.from(bytes.slice(0, 2))).toEqual([0x50, 0x4b])
    const zip = await JSZip.loadAsync(bytes)
    const documentXml = await zip.file('word/document.xml')!.async('text')
    const headerXml = await zip.file('word/header1.xml')!.async('text')
    const footerXml = await zip.file('word/footer1.xml')!.async('text')
    const relationships = await zip.file('word/_rels/document.xml.rels')!.async('text')

    expect(documentXml).toContain('Product ')
    expect(documentXml).toContain('leader')
    expect(documentXml).toContain('w:type="page"')
    expect(documentXml).toContain('w:pgSz w:w="11906" w:h="16838"')
    expect(documentXml).toContain('w:pgMar w:top="720" w:right="1080" w:bottom="1440" w:left="1800"')
    expect(headerXml).toContain('Example User')
    expect(footerXml).toContain('Draft')
    expect(footerXml).toContain('PAGE')
    expect(relationships).toContain('https://example.com/profile')
  })

  it('is deterministic at the semantic XML level for the same revision', async () => {
    const document = documentFixture()
    const first = await JSZip.loadAsync(await exportEditableDocumentDocx(document))
    const second = await JSZip.loadAsync(await exportEditableDocumentDocx(document))
    const firstXml = await first.file('word/document.xml')!.async('text')
    const secondXml = await second.file('word/document.xml')!.async('text')
    const normalizeRelationshipIds = (xml: string) => xml.replaceAll(/r:id="[^"]+"/g, 'r:id="RELATIONSHIP"')
    expect(normalizeRelationshipIds(firstXml)).toBe(normalizeRelationshipIds(secondXml))
  })

  it('serializes lists, tables, images, and re-imports the supported semantic projection', async () => {
    const document = documentFixture()
    const section = document.content.content?.[1]
    if (!section?.content) throw new Error('Fixture section missing')
    section.content.push(
      {
        type: 'bulletList',
        content: [{ type: 'listItem', attrs: blockAttrs(), content: [{ type: 'paragraph', attrs: blockAttrs(), content: [{ type: 'text', text: 'Shipped reliably' }] }] }]
      },
      {
        type: 'table',
        attrs: blockAttrs(),
        content: [{ type: 'tableRow', content: [{ type: 'tableCell', content: [{ type: 'paragraph', attrs: blockAttrs(), content: [{ type: 'text', text: 'Metric' }] }] }] }]
      },
      {
        type: 'image',
        attrs: { ...blockAttrs(), src: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', alt: 'One pixel', width: 1, height: 1 }
      }
    )

    const bytes = await exportEditableDocumentDocx(document)
    const zip = await JSZip.loadAsync(bytes)
    const xml = await zip.file('word/document.xml')!.async('text')
    expect(xml).toContain('<w:numPr>')
    expect(xml).toContain('<w:tbl>')
    expect(xml).toContain('<w:drawing>')
    expect(Object.keys(zip.files).some(name => name.startsWith('word/media/'))).toBe(true)

    const imported = await importDocx(bytes, 'jobos-roundtrip.docx', 'resume', new Date('2026-08-07T00:00:00Z'))
    const projection = plainText(imported.content)
    expect(projection).toContain('Product leader')
    expect(projection).toContain('Shipped reliably')
    expect(projection).toContain('Metric')
  })

  it('refuses unresolved suggestions before creating bytes', async () => {
    const document = documentFixture()
    const summary = document.content.content?.[1]?.content?.[0]
    if (!summary) throw new Error('Fixture summary missing')
    summary.content = [{ type: 'text', text: 'Pending', marks: [{ type: 'suggestion', attrs: { suggestionId: 'sug_pending', kind: 'insert', author: 'user', createdAt: '2026-08-07T00:00:00Z' } }] }]
    await expect(exportEditableDocumentDocx(document)).rejects.toThrow('Resolve every suggestion')
  })
})
