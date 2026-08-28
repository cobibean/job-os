import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import JSZip from 'jszip'
import { describe, expect, it } from 'vitest'

import { validateEditableContent } from '../../../../shared/editableDocumentSchema.js'
import type { DocumentKey, TiptapNodeJson } from '../../../../shared/editableDocuments.js'
import { importDocx, MAX_DOCX_BYTES } from './docxImporter.js'
import { sanitizeImportedHtml } from './sanitizeImportedHtml.js'
import { advertisedEntryCountOverflowDocx, advertisedZipBombDocx, entityExpansionDocx, missingDocumentEntryDocx } from './fixtures/attackFixtures.js'

const fixtureDirectory = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')
const fixture = (name: string) => readFile(path.join(fixtureDirectory, name))
const fixedNow = new Date('2026-08-07T12:00:00.000Z')

function walk(node: TiptapNodeJson): TiptapNodeJson[] {
  return [node, ...(node.content ?? []).flatMap(walk)]
}

async function imported(name: string, key: DocumentKey) {
  return importDocx(await fixture(name), name, key, fixedNow)
}

describe('bounded DOCX importer', () => {
  it.each([
    ['one-page-resume.docx', 'resume'],
    ['cover-letter-header-footer.docx', 'cover_letter'],
    ['references-sheet.docx', 'references']
  ] as const)('imports real %s fixture as %s canonical content', async (name, key) => {
    const result = await imported(name, key)
    expect(result.content.type).toBe('doc')
    expect(result.content.content?.[0]?.type).toBe('jobosSection')
    expect(JSON.stringify(result.content)).toContain(key === 'references' ? 'References' : 'Alex Morgan')
    expect(result.importReport).toEqual(expect.objectContaining({ sourceFilename: name, importedAt: fixedNow.toISOString() }))
    validateEditableContent(result.content, result.settings, [], result.importReport)
  })

  it('extracts first-section settings, simple headers/footers, tables, lists, and explicit page breaks', async () => {
    const coverLetter = await imported('cover-letter-header-footer.docx', 'cover_letter')
    expect(coverLetter.settings).toMatchObject({
      pageSize: 'letter',
      marginsInches: { top: 0.75, right: 0.75, bottom: 0.75, left: 0.75 },
      header: { center: 'Alex Morgan — Cover Letter' },
      footer: { center: 'Confidential application' }
    })
    const resume = await imported('two-page-resume-table.docx', 'resume')
    const types = walk(resume.content).map(node => node.type)
    expect(types).toContain('table')
    expect(types).toContain('bulletList')
    expect(types).toContain('pageBreak')
    validateEditableContent(resume.content, resume.settings, [], resume.importReport)
  })

  it('assigns valid deterministic stable IDs and semantic section roles', async () => {
    const bytes = await fixture('one-page-resume.docx')
    const first = await importDocx(bytes, 'one-page-resume.docx', 'resume', fixedNow)
    const second = await importDocx(bytes, 'one-page-resume.docx', 'resume', fixedNow)
    const ids = walk(first.content).flatMap(node => typeof node.attrs?.jobosId === 'string' ? [node.attrs.jobosId] : [])
    expect(ids.length).toBeGreaterThan(5)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids.every(id => /^node_[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id))).toBe(true)
    expect(second.content).toEqual(first.content)
    expect(walk(first.content).some(node => node.attrs?.semanticRole === 'experience')).toBe(true)
  })

  it('removes scripts, event handlers, unsafe URLs, CSS URLs, and remote images', async () => {
    const dirty = '<script>alert(1)</script><p onclick="evil()" style="text-align:center;background-image:url(file:///x)">Safe <a href="javascript:evil()">link</a><a href="https://example.com">web</a><img src="https://evil.test/x.png" onerror="evil()"><img src="data:text/html;base64,WA=="></p>'
    const clean = sanitizeImportedHtml(dirty)
    expect(clean).toContain('Safe')
    expect(clean).toContain('https://example.com')
    expect(clean).toContain('text-align:center')
    expect(clean).not.toMatch(/script|onclick|javascript:|background-image|file:|evil\.test|data:text\/html|onerror/i)

    await expect(imported('unsafe-content.docx', 'resume')).rejects.toThrow('unsafe external hyperlink')
  })

  it('persists visible warnings for flattened tracked changes and dropped embedded objects', async () => {
    const tracked = await imported('tracked-changes.docx', 'resume')
    expect(tracked.importReport.issues).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'tracked_changes_flattened', severity: 'normalized' })
    ]))
    expect(JSON.stringify(tracked.content)).toContain('Product engineer')
    expect(JSON.stringify(tracked.content)).not.toContain('Old summary')

    const unsupported = await imported('unsupported-objects.docx', 'resume')
    expect(unsupported.importReport.issues).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'unsupported_objects_dropped', severity: 'dropped' })
    ]))
    validateEditableContent(unsupported.content, unsupported.settings, [], unsupported.importReport)
  })

  it('rejects missing required entries, malformed/non-ZIP bytes, docm, and oversized input', async () => {
    await expect(importDocx(await missingDocumentEntryDocx(), 'missing.docx', 'resume', fixedNow)).rejects.toThrow('word/document.xml')
    await expect(importDocx(Buffer.from('not a zip'), 'bad.docx', 'resume', fixedNow)).rejects.toThrow('ZIP-based DOCX')
    await expect(importDocx(await fixture('one-page-resume.docx'), 'macro.docm', 'resume', fixedNow)).rejects.toThrow('.docx')
    await expect(importDocx(new Uint8Array(MAX_DOCX_BYTES + 1), 'large.docx', 'resume', fixedNow)).rejects.toThrow('20 MB')
  })

  it('accepts normal Word styles-with-effects content and tel hyperlinks', async () => {
    const wordDocx = await JSZip.loadAsync(await fixture('one-page-resume.docx'))
    const contentTypes = await wordDocx.file('[Content_Types].xml')!.async('string')
    wordDocx.file(
      '[Content_Types].xml',
      contentTypes.replace('</Types>', '<Default Extension="stylesWithEffects" ContentType="application/vnd.ms-word.stylesWithEffects+xml"/></Types>')
    )
    const relationships = await wordDocx.file('word/_rels/document.xml.rels')!.async('string')
    wordDocx.file(
      'word/_rels/document.xml.rels',
      relationships.replace('</Relationships>', '<Relationship Id="phone" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="tel:+17125553500" TargetMode="External"/></Relationships>')
    )

    const result = await importDocx(await wordDocx.generateAsync({ type: 'uint8array' }), 'normal-word.docx', 'resume', fixedNow)
    validateEditableContent(result.content, result.settings, [], result.importReport)
  })

  it('still rejects macro-enabled package content types', async () => {
    const macroDocx = await JSZip.loadAsync(await fixture('one-page-resume.docx'))
    const contentTypes = await macroDocx.file('[Content_Types].xml')!.async('string')
    macroDocx.file(
      '[Content_Types].xml',
      contentTypes.replace(
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml',
        'application/vnd.ms-word.document.macroEnabled.main+xml'
      )
    )
    await expect(importDocx(await macroDocx.generateAsync({ type: 'uint8array' }), 'macro-content.docx', 'resume', fixedNow)).rejects.toThrow('Macro-enabled')
  })

  it('rejects the advertised ZIP bomb budget before extraction and rejects XML entities', async () => {
    await expect(importDocx(await advertisedZipBombDocx(), 'bomb.docx', 'resume', fixedNow)).rejects.toThrow('uncompressed budget')
    await expect(importDocx(await advertisedEntryCountOverflowDocx(), 'entries.docx', 'resume', fixedNow)).rejects.toThrow('entry limit')
    await expect(importDocx(await entityExpansionDocx(), 'entity.docx', 'resume', fixedNow)).rejects.toThrow('Unsafe XML declaration')
  })

  it('rejects unsafe relationships anywhere in the OOXML package', async () => {
    const bytes = await fixture('one-page-resume.docx')
    const external = await JSZip.loadAsync(bytes)
    const externalRels = await external.file('word/_rels/document.xml.rels')!.async('string')
    external.file(
      'word/_rels/document.xml.rels',
      externalRels.replace('</Relationships>', '<Relationship Id="evil" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="https://evil.test/pixel.png" TargetMode="External"/></Relationships>')
    )
    await expect(importDocx(await external.generateAsync({ type: 'uint8array' }), 'external.docx', 'resume', fixedNow)).rejects.toThrow('disallowed external relationship')

    const hostileHyperlink = await JSZip.loadAsync(bytes)
    const hyperlinkRels = await hostileHyperlink.file('word/_rels/document.xml.rels')!.async('string')
    hostileHyperlink.file(
      'word/_rels/document.xml.rels',
      hyperlinkRels.replace('</Relationships>', '<Relationship Id="hostile-link" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="javascript:alert(1)" TargetMode="External"/></Relationships>')
    )
    await expect(importDocx(await hostileHyperlink.generateAsync({ type: 'uint8array' }), 'hostile-link.docx', 'resume', fixedNow)).rejects.toThrow('unsafe external hyperlink')

    const traversal = await JSZip.loadAsync(bytes)
    traversal.file(
      'word/_rels/header1.xml.rels',
      '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="evil" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../../../outside.png"/></Relationships>'
    )
    await expect(importDocx(await traversal.generateAsync({ type: 'uint8array' }), 'traversal.docx', 'resume', fixedNow)).rejects.toThrow('unsafe internal relationship target')
  })
})
