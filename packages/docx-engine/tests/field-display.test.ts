import { describe, expect, it } from 'vitest'
import { generateTocFieldXml, parseDocx } from '../src/index'
import { buildDocx } from './helpers/build-docx'

// TOC entry paragraph as Word writes it: TOC field begin + hyperlink entry
// with a dot-leader tab and a nested PAGEREF field for the page number.
const TOC_ENTRY_PARAGRAPH =
  '<w:p><w:pPr><w:pStyle w:val="TOC1"/>' +
  '<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9350"/></w:tabs></w:pPr>' +
  '<w:r><w:fldChar w:fldCharType="begin"/></w:r>' +
  '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-3" \\h </w:instrText></w:r>' +
  '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' +
  '<w:hyperlink w:anchor="_Toc1" w:history="1">' +
  '<w:r><w:t>第一章 概述</w:t></w:r>' +
  '<w:r><w:tab/></w:r>' +
  '<w:r><w:fldChar w:fldCharType="begin"/></w:r>' +
  '<w:r><w:instrText xml:space="preserve"> PAGEREF _Toc1 \\h </w:instrText></w:r>' +
  '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' +
  '<w:r><w:t>2</w:t></w:r>' +
  '<w:r><w:fldChar w:fldCharType="end"/></w:r>' +
  '</w:hyperlink></w:p>'

const FIELD_END_PAGEBREAK_PARAGRAPH =
  '<w:p><w:r><w:fldChar w:fldCharType="end"/></w:r><w:r><w:br w:type="page"/></w:r></w:p>'

const PAGE_FIELD_PARAGRAPH =
  '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>' +
  '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>' +
  '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' +
  '<w:r><w:t>- 8 -</w:t></w:r>' +
  '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'

describe('field paragraph display model', () => {
  it('TOC entry becomes a tocLine with title, page number and level', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: TOC_ENTRY_PARAGRAPH }))
    const block = doc.blocks[0]
    expect(block.type).toBe('passthrough')
    expect(block.fieldDisplay).toEqual({
      kind: 'tocLine',
      left: '第一章 概述',
      right: '2',
      level: 1,
      anchor: '_Toc1',
    })
  })

  it('field-end + page break paragraph shows as a pageBreak marker', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: FIELD_END_PAGEBREAK_PARAGRAPH }))
    expect(doc.blocks[0].fieldDisplay).toEqual({ kind: 'pageBreak' })
  })

  it('PAGE field paragraphs collapse into an editable inline field run (cached result as display text)', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: PAGE_FIELD_PARAGRAPH }))
    expect(doc.blocks[0].type).toBe('paragraph')
    expect(doc.blocks[0].runs?.[0]).toMatchObject({ text: '- 8 -', instrField: 'PAGE' })
  })

  it('TOC entry carries its hyperlink anchor for click-to-jump', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: TOC_ENTRY_PARAGRAPH }))
    expect(doc.blocks[0].fieldDisplay?.anchor).toBe('_Toc1')
  })
})

describe('generateTocFieldXml', () => {
  const entries = [
    { level: 1, text: '第一章 概述' },
    { level: 2, text: '1.1 背景 <特殊&字符>' },
    { level: 1, text: '第二章 分析' },
  ]

  it('emits a dirty TOC field spanning one paragraph per entry', () => {
    const frags = generateTocFieldXml(entries)
    expect(frags).toHaveLength(3)
    // field structure: dirty begin + instruction in the first, single end in the last
    expect(frags[0]).toContain('w:fldCharType="begin" w:dirty="true"')
    expect(frags[0]).toContain(' TOC \\o "1-2" \\h \\z \\u ')
    expect(frags[0]).toContain('<w:pStyle w:val="TOC1"/>')
    expect(frags[1]).toContain('<w:pStyle w:val="TOC2"/>')
    expect(frags[1]).toContain('&lt;特殊&amp;字符&gt;')
    expect(frags[2]).toContain('w:fldCharType="end"')
    const joined = frags.join('')
    expect(joined.match(/w:fldCharType="end"/g)).toHaveLength(1)
    expect(joined.match(/w:fldCharType="begin"/g)).toHaveLength(1)
  })

  it('round-trips through the parser as tocLine display blocks', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: generateTocFieldXml(entries).join('') }))
    const visible = doc.blocks.filter((b) => !b.hidden)
    expect(visible).toHaveLength(3)
    for (const [i, block] of visible.entries()) {
      expect(block.type).toBe('passthrough')
      expect(block.fieldDisplay?.kind).toBe('tocLine')
      expect(block.fieldDisplay?.left).toBe(entries[i].text)
      expect(block.fieldDisplay?.level).toBe(entries[i].level)
    }
  })
})
