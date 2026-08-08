import { describe, expect, it } from 'vitest'
import { mergePPrFormat, parseDocx, saveDocx } from '../src/index'
import { buildDocx } from './helpers/build-docx'

const BIDI_BODY =
  '<w:p><w:pPr><w:bidi/><w:jc w:val="right"/></w:pPr><w:r><w:t>כותרת</w:t></w:r></w:p>' +
  '<w:p><w:pPr><w:bidi/><w:jc w:val="left"/></w:pPr><w:r><w:t>שמאל</w:t></w:r></w:p>' +
  '<w:p><w:pPr><w:bidi/></w:pPr><w:r><w:t>ברירת מחדל</w:t></w:r></w:p>' +
  '<w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:t>LTR right</w:t></w:r></w:p>'

describe('RTL paragraphs (w:bidi + Word jc left/right swap)', () => {
  it('parses bidi and stores the visual alignment (jc left/right swapped)', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: BIDI_BODY }))
    expect(doc.blocks[0].format).toMatchObject({ bidi: true, align: 'left' })
    expect(doc.blocks[1].format).toMatchObject({ bidi: true, align: 'right' })
    expect(doc.blocks[2].format?.bidi).toBe(true)
    expect(doc.blocks[2].format?.align).toBeUndefined()
    // non-bidi paragraphs are not swapped
    expect(doc.blocks[3].format?.bidi).toBeUndefined()
    expect(doc.blocks[3].format?.align).toBe('right')
  })

  it('keeps untouched bidi paragraphs byte-identical', async () => {
    const bytes = await buildDocx({ bodyXml: BIDI_BODY })
    const doc = await parseDocx(bytes)
    const blocks = doc.blocks
      .filter((b) => !b.hidden && b.docxIndex !== null)
      .map((b) => ({ kind: 'original' as const, docxIndex: b.docxIndex! }))
    expect(await saveDocx(doc, blocks)).toEqual(bytes)
  })

  it('mergePPrFormat writes the logical jc back and keeps a single w:bidi', () => {
    const out = mergePPrFormat('<w:pPr><w:bidi/><w:jc w:val="right"/></w:pPr>', {
      bidi: true,
      align: 'left',
    })
    expect(out.match(/<w:bidi\/>/g)).toHaveLength(1)
    expect(out).toContain('<w:jc w:val="right"/>')
    // visual right maps back to logical left
    const out2 = mergePPrFormat('<w:pPr><w:bidi/><w:jc w:val="left"/></w:pPr>', {
      bidi: true,
      align: 'right',
    })
    expect(out2).toContain('<w:jc w:val="left"/>')
  })
})

describe('RTL tables (tblPr w:bidiVisual)', () => {
  it('parses bidiVisual into the table model', async () => {
    const xml =
      '<w:tbl><w:tblPr><w:bidiVisual/><w:tblW w:w="0" w:type="auto"/></w:tblPr>' +
      '<w:tblGrid><w:gridCol w:w="4000"/></w:tblGrid>' +
      '<w:tr><w:tc><w:p><w:r><w:t>א</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    const doc = await parseDocx(await buildDocx({ bodyXml: xml }))
    expect(doc.blocks[0].table?.bidiVisual).toBe(true)
  })
})
