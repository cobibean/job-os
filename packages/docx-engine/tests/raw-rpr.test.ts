/**
 * rawRPr passthrough: run-level unmodeled properties (caps/vanish/dstrike/bdr/double
 * underline/themeColor/character spacing/all four rFonts slots…) survive paragraph
 * rebuilds; editing a modeled field rebuilds only its group.
 */
import { describe, expect, it } from 'vitest'
import { generateParagraphXml, parseDocx, saveDocx, type GenerateContext, type SaveBlock } from '../src/index'
import { buildDocx } from './helpers/build-docx'
import { existsSync, readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const GEN_CTX: GenerateContext = {
  headingStyleIds: new Map([[1, 'Heading1']]),
  allocateHyperlinkRel: () => 'rId999',
}

// A run packed with unmodeled properties: double underline, caps, hidden text, double
// strikethrough, character border, run-level shading, character spacing, horizontal
// scale, position, kern, lang, themeColor, szCs≠sz, all four rFonts slots
const RICH_RPR =
  '<w:rPr>' +
  '<w:rFonts w:ascii="Georgia" w:eastAsia="宋体" w:hAnsi="Georgia" w:cs="Arial"/>' +
  '<w:caps/><w:dstrike/><w:vanish/>' +
  '<w:color w:val="4472C4" w:themeColor="accent1"/>' +
  '<w:spacing w:val="20"/><w:w w:val="90"/><w:kern w:val="24"/><w:position w:val="6"/>' +
  '<w:sz w:val="28"/><w:szCs w:val="32"/>' +
  '<w:u w:val="double"/>' +
  '<w:bdr w:val="single" w:sz="4" w:space="1" w:color="auto"/>' +
  '<w:shd w:val="clear" w:color="auto" w:fill="FFF2CC"/>' +
  '<w:lang w:val="en-US" w:eastAsia="zh-CN"/>' +
  '</w:rPr>'
const RICH_PARA = `<w:p><w:r>${RICH_RPR}<w:t>富格式文本</w:t></w:r></w:p>`

const UNMODELED_TAGS = [
  '<w:caps/>', '<w:dstrike/>', '<w:vanish/>',
  'w:themeColor="accent1"',
  '<w:spacing w:val="20"/>', '<w:w w:val="90"/>', '<w:kern w:val="24"/>', '<w:position w:val="6"/>',
  '<w:szCs w:val="32"/>',
  '<w:u w:val="double"/>',
  '<w:bdr w:val="single" w:sz="4" w:space="1" w:color="auto"/>',
  '<w:shd w:val="clear" w:color="auto" w:fill="FFF2CC"/>',
  '<w:lang w:val="en-US" w:eastAsia="zh-CN"/>',
  'w:cs="Arial"',
]

describe('rawRPr passthrough', () => {
  it('text-only edit keeps every unmodeled rPr property', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: RICH_PARA }))
    const run = doc.blocks[0].runs![0]
    expect(run.rawRPr).toBeTruthy()
    // Text-only edit: formatting fields carry over verbatim
    const xml = generateParagraphXml(
      { type: 'paragraph', runs: [{ ...run, text: '改过的文字' }] },
      GEN_CTX,
    )
    for (const tag of UNMODELED_TAGS) expect(xml, tag).toContain(tag)
    expect(xml).toContain('<w:t xml:space="preserve">改过的文字</w:t>')
  })

  it('editing a modeled field rebuilds only that group, keeps the rest', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: RICH_PARA }))
    const run = doc.blocks[0].runs![0]
    // Add bold (not there originally): w:b is inserted at its schema position, the other
    // groups keep their original bytes
    const xml = generateParagraphXml(
      { type: 'paragraph', runs: [{ ...run, bold: true }] },
      GEN_CTX,
    )
    expect(xml).toContain('<w:b/>')
    // schema order: rFonts < b < caps
    expect(xml.indexOf('<w:rFonts')).toBeLessThan(xml.indexOf('<w:b/>'))
    expect(xml.indexOf('<w:b/>')).toBeLessThan(xml.indexOf('<w:caps/>'))
    for (const tag of UNMODELED_TAGS) expect(xml, tag).toContain(tag)
  })

  it('double underline survives while modeled underline stays true, drops when cleared', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: RICH_PARA }))
    const run = doc.blocks[0].runs![0]
    expect(run.underline).toBe(true) // the parse side counts double as underline
    const kept = generateParagraphXml({ type: 'paragraph', runs: [{ ...run }] }, GEN_CTX)
    expect(kept).toContain('<w:u w:val="double"/>') // not edited → double is kept
    const cleared = generateParagraphXml(
      { type: 'paragraph', runs: [{ ...run, underline: false }] },
      GEN_CTX,
    )
    expect(cleared).not.toContain('<w:u') // underline cleared → whole group removed
  })

  it('changing size rebuilds sz+szCs together; changing color drops stale themeColor', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: RICH_PARA }))
    const run = doc.blocks[0].runs![0]
    const resized = generateParagraphXml(
      { type: 'paragraph', runs: [{ ...run, sizeHalfPoints: 36 }] },
      GEN_CTX,
    )
    expect(resized).toContain('<w:sz w:val="36"/>')
    expect(resized).toContain('<w:szCs w:val="36"/>')
    expect(resized).not.toContain('w:val="32"')
    const recolored = generateParagraphXml(
      { type: 'paragraph', runs: [{ ...run, color: 'FF0000' }] },
      GEN_CTX,
    )
    expect(recolored).toContain('<w:color w:val="FF0000"/>')
    expect(recolored).not.toContain('themeColor') // after an explicit recolor the theme-color reference no longer holds
  })

  it('adjacent runs with different unmodeled props are not merged at parse', async () => {
    const body =
      '<w:p><w:r><w:rPr><w:caps/></w:rPr><w:t>大写</w:t></w:r>' +
      '<w:r><w:t>普通</w:t></w:r></w:p>'
    const doc = await parseDocx(await buildDocx({ bodyXml: body }))
    expect(doc.blocks[0].runs).toHaveLength(2) // modeled fields all equal, but rawRPr differs → no merge
  })
})

const here = dirname(fileURLToPath(import.meta.url))
const REAL = join(here, '..', '..', '..', 'example', 'docx', 'OpenClaw开源项目调研分析报告文档.docx')

// Runs only when the local (gitignored) sample document is present.
describe.skipIf(!existsSync(REAL))('real document invariant (example/docx)', () => {
  it('edit one paragraph: all other zip entries and w:p slices stay byte-identical, edited one keeps char spacing', async () => {
    const bytes = new Uint8Array(readFileSync(REAL))
    const doc = await parseDocx(bytes)
    const visible = doc.blocks.filter((b) => !b.hidden)
    // Pick the first editable paragraph with a run-level w:spacing (character spacing, unmodeled)
    const target = visible.find(
      (b) =>
        (b.type === 'paragraph' || b.type === 'heading') &&
        b.runs?.length &&
        b.runs.some((r) => r.rawRPr?.includes('<w:spacing')),
    )!
    expect(target).toBeTruthy()

    const finalBlocks: SaveBlock[] = visible.map((b) =>
      b === target
        ? {
            kind: 'generated',
            block: {
              type: b.type as 'paragraph' | 'heading',
              ...(b.level ? { level: b.level } : {}),
              ...(b.styleId ? { styleId: b.styleId } : {}),
              ...(b.rawPPr !== undefined ? { rawPPr: b.rawPPr } : {}),
              ...(b.bookmarks ? { bookmarks: b.bookmarks } : {}),
              ...(b.hiddenBookmarks ? { hiddenBookmarks: b.hiddenBookmarks } : {}),
              runs: b.runs!.map((r, i) => (i === 0 ? { ...r, text: '【已编辑】' + r.text } : r)),
            },
          }
        : { kind: 'original', docxIndex: b.docxIndex! },
    )
    const saved = await saveDocx(doc, finalBlocks)

    // Other zip entries stay byte-identical
    const JSZip = (await import('jszip')).default
    const [zBefore, zAfter] = await Promise.all([JSZip.loadAsync(bytes), JSZip.loadAsync(saved)])
    const fileNames = (z: InstanceType<typeof JSZip>) =>
      Object.keys(z.files).filter((n) => !z.files[n].dir).sort()
    expect(fileNames(zAfter)).toEqual(fileNames(zBefore))
    for (const name of fileNames(zBefore)) {
      // core.xml is the exception: a real save updates dcterms:modified / cp:revision
      if (name === 'word/document.xml' || name === 'docProps/core.xml') continue
      const [a, b] = await Promise.all([
        zBefore.files[name].async('uint8array'),
        zAfter.files[name].async('uint8array'),
      ])
      expect(b, name).toEqual(a)
    }

    // Untouched paragraphs' original slices remain; the edited one keeps its character spacing
    const newXml = new TextDecoder().decode(await zAfter.files['word/document.xml'].async('uint8array'))
    let checked = 0
    for (const b of visible) {
      if (b === target || !b.originalXml || b.originalXml.length < 40) continue
      expect(newXml.includes(b.originalXml), `block docxIndex=${b.docxIndex}`).toBe(true)
      checked++
    }
    expect(checked).toBeGreaterThan(10)
    expect(newXml).toContain('【已编辑】')
    const spacingVal = /<w:spacing w:val="[^"]+"\/>/.exec(target.runs![0].rawRPr!)?.[0]
    expect(spacingVal).toBeTruthy()
    // Character spacing is still in the edited paragraph (lost here before the rawRPr fix)
    const editedPara = /<w:p>(?:(?!<\/w:p>).)*【已编辑】(?:(?!<\/w:p>).)*<\/w:p>/s.exec(newXml)?.[0]
    expect(editedPara).toBeTruthy()
    expect(editedPara!).toContain(spacingVal!)

    // The saved output can be re-parsed
    const reparsed = await parseDocx(saved)
    expect(reparsed.blocks.length).toBe(doc.blocks.length)
  })
})
