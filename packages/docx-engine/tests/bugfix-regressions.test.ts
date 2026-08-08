import JSZip from 'jszip'
import { describe, expect, it } from 'vitest'
import {
  applySectionSettings,
  buildChartWorkbookXlsxBase64,
  latexToOmml,
  nextNoteId,
  ommlToLatex,
  parseDocx,
  patchChartWorkbookXlsxBase64,
  readSections,
  saveDocx,
  sectionSettingsFromXml,
  type SaveBlock,
} from '../src/index'
import { patchParagraphTexts } from '../src/text-patch'
import { escapeXmlText } from '../src/xml-utils'
import { buildDocx } from './helpers/build-docx'

const P = (text: string) => `<w:p><w:r><w:t>${text}</w:t></w:r></w:p>`

describe('saveDocx isUnchanged short-circuit', () => {
  it('a sectionStartType-only edit is not silently dropped', async () => {
    const bytes = await buildDocx({ bodyXml: P('a') + P('b') })
    const parsed = await parseDocx(bytes)
    const visible: SaveBlock[] = parsed.blocks
      .filter((b) => !b.hidden)
      .map((b) => ({ kind: 'original', docxIndex: b.docxIndex! }))
    const saved = await saveDocx(parsed, visible, { sectionStartType: 'continuous' })
    expect(saved).not.toBe(bytes)
    const sections = readSections(await parseDocx(saved))
    expect(sections[0].startType).toBe('continuous')
  })
})

describe('applySectionSettings w:cols', () => {
  const SECT_PR =
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>' +
    '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>' +
    '<w:cols w:num="3" w:space="720">' +
    '<w:col w:w="3000" w:space="720"/><w:col w:w="3600" w:space="720"/><w:col w:w="2000"/>' +
    '</w:cols></w:sectPr>'

  it('keeps explicit column widths when the count is unchanged', () => {
    const settings = sectionSettingsFromXml(SECT_PR)
    expect(settings.columns).toBe(3)
    const out = applySectionSettings(SECT_PR, settings)
    expect(out.match(/<w:cols[\s>]/g)?.length).toBe(1)
    expect(out).toContain('<w:col w:w="3000"')
  })

  it('replaces the open-form element without duplicating it when the count changes', () => {
    const settings = sectionSettingsFromXml(SECT_PR)
    const out = applySectionSettings(SECT_PR, { ...settings, columns: 2 })
    expect(out.match(/<w:cols[\s>]/g)?.length).toBe(1)
    expect(out).toContain('w:num="2"')
    expect(out).not.toContain('<w:col w:w=')
  })
})

describe('patchParagraphTexts with nested textbox paragraphs', () => {
  const ENTRY =
    '<w:comment w:id="1">' +
    '<w:p><w:r><w:t xml:space="preserve">outer </w:t></w:r>' +
    '<w:r><w:drawing><wps:txbx><w:txbxContent>' +
    '<w:p><w:r><w:t>inside box</w:t></w:r></w:p>' +
    '</w:txbxContent></wps:txbx></w:drawing></w:r>' +
    '<w:r><w:t>tail</w:t></w:r></w:p>' +
    '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>second</w:t></w:r></w:p>' +
    '</w:comment>'

  it('slices the outer paragraph past the embedded w:p and patches surgically', () => {
    const out = patchParagraphTexts(ENTRY, 'outer inside boxtail\nsecond edited')
    expect(out).not.toBeNull()
    // the textbox structure of the untouched paragraph survives byte-for-byte
    expect(out).toContain(
      '<w:txbxContent><w:p><w:r><w:t>inside box</w:t></w:r></w:p></w:txbxContent>',
    )
    expect(out).toContain('second edited')
    expect(out).toContain('<w:rPr><w:b/></w:rPr>')
  })
})

describe('escapeXmlText', () => {
  it('strips control characters that are illegal in XML 1.0 but keeps tab/newline', () => {
    expect(escapeXmlText('a\u0000b\u000Bc\td\ne')).toBe('abc\td\ne')
    expect(escapeXmlText('<a & b>')).toBe('&lt;a &amp; b&gt;')
  })
})

describe('nextNoteId', () => {
  it('starts at 1 for a document with no existing notes', () => {
    expect(nextNoteId([])).toBe('1')
  })
})

describe('ommlToLatex matrix environments', () => {
  it('recovers the named pmatrix environment instead of generic delimiters', () => {
    const omml = `<m:oMath>${latexToOmml('\\begin{pmatrix} 1 & 0 \\\\ 0 & 1 \\end{pmatrix}')}</m:oMath>`
    expect(ommlToLatex(omml)).toContain('\\begin{pmatrix}')
  })
})

describe('patchChartWorkbookXlsxBase64', () => {
  it('preserves unrelated workbook parts and updates Sheet1 in place', async () => {
    const base = await buildChartWorkbookXlsxBase64(['a', 'b'], [{ name: 'S1', values: [1, 2] }])
    const zip = await JSZip.loadAsync(Buffer.from(base, 'base64'))
    zip.file('xl/styles.xml', '<styleSheet/>')
    zip.file('xl/worksheets/sheet2.xml', '<worksheet><sheetData/></worksheet>')
    const withExtras = Buffer.from(await zip.generateAsync({ type: 'uint8array' })).toString(
      'base64',
    )

    const patched = await patchChartWorkbookXlsxBase64(
      withExtras,
      ['x', 'y'],
      [{ name: 'S1', values: [9, 8] }],
    )
    expect(patched).not.toBeNull()
    const outZip = await JSZip.loadAsync(Buffer.from(patched!, 'base64'))
    expect(outZip.file('xl/styles.xml')).toBeTruthy()
    expect(outZip.file('xl/worksheets/sheet2.xml')).toBeTruthy()
    const sheet = await outZip.file('xl/worksheets/sheet1.xml')!.async('string')
    expect(sheet).toContain('<v>9</v>')
    expect(sheet).toContain('>x</t>')
  })
})
