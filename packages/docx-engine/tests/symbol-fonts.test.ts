import { describe, expect, it } from 'vitest'
import { decodeSymbolChar, decodeSymbolText, isSymbolFont, parseDocx } from '../src/index'
import { buildDocx } from './helpers/build-docx'

const XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

describe('symbol font decoding helpers', () => {
  it('recognizes symbol-encoded fonts case-insensitively', () => {
    expect(isSymbolFont('Wingdings')).toBe(true)
    expect(isSymbolFont('symbol')).toBe(true)
    expect(isSymbolFont('Webdings')).toBe(true)
    expect(isSymbolFont('Calibri')).toBe(false)
    expect(isSymbolFont(null)).toBe(false)
  })

  it('maps raw bytes and their U+F0xx private-use forms alike', () => {
    expect(decodeSymbolChar('Wingdings', 0x6c)).toBe('●')
    expect(decodeSymbolChar('Wingdings', 0xf06c)).toBe('●')
    expect(decodeSymbolChar('Symbol', 0xf0b7)).toBe('•')
    expect(decodeSymbolChar('Wingdings', 0xf000)).toBeNull()
  })

  it('decodes whole strings only when every glyph maps', () => {
    expect(decodeSymbolText('Symbol', 'abg')).toBe('αβγ')
    expect(decodeSymbolText('Wingdings', 'l e')).toBeNull()
    expect(decodeSymbolText('Calibri', 'abc')).toBeNull()
  })
})

describe('symbol runs in document.xml', () => {
  it('converts w:sym to the Unicode equivalent', async () => {
    const bytes = await buildDocx({
      bodyXml:
        '<w:p><w:r><w:t>勾:</w:t></w:r><w:r><w:sym w:font="Wingdings" w:char="F0FC"/></w:r>' +
        '<w:r><w:sym w:font="Wingdings" w:char="6C"/></w:r></w:p>',
    })
    const doc = await parseDocx(bytes)
    expect(doc.blocks[0].runs!.map((r) => r.text).join('')).toBe('勾:✓●')
  })

  it('keeps unknown w:sym glyphs as private-use characters', async () => {
    const bytes = await buildDocx({
      bodyXml: '<w:p><w:r><w:sym w:font="Wingdings 2" w:char="F045"/></w:r></w:p>',
    })
    const doc = await parseDocx(bytes)
    expect(doc.blocks[0].runs!.map((r) => r.text).join('')).toBe('\uF045')
  })

  it('decodes text runs carrying a symbol font and drops the font', async () => {
    const bytes = await buildDocx({
      bodyXml:
        '<w:p><w:r><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:t>&#xF0B7;</w:t></w:r>' +
        '<w:r><w:t> 后文</w:t></w:r></w:p>',
    })
    const doc = await parseDocx(bytes)
    const run = doc.blocks[0].runs![0]
    expect(doc.blocks[0].runs!.map((r) => r.text).join('')).toBe('• 后文')
    expect(run.font).toBeUndefined()
    expect(run.rawRPr ?? '').not.toContain('w:rFonts')
  })

  it('leaves undecodable symbol-font runs untouched', async () => {
    const bytes = await buildDocx({
      bodyXml:
        '<w:p><w:r><w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/></w:rPr><w:t>le</w:t></w:r></w:p>',
    })
    const doc = await parseDocx(bytes)
    const run = doc.blocks[0].runs![0]
    expect(run.text).toBe('le')
    expect(run.font).toBe('Wingdings')
  })

  it('decodes numeric character references in w:t', async () => {
    const bytes = await buildDocx({
      bodyXml: '<w:p><w:r><w:t>&#x2713;&#65;</w:t></w:r></w:p>',
    })
    const doc = await parseDocx(bytes)
    expect(doc.blocks[0].runs!.map((r) => r.text).join('')).toBe('✓A')
  })
})

describe('numbering level marker font', () => {
  it('parses w:rPr/w:rFonts of a bullet level', async () => {
    const numberingXml =
      XML_DECL +
      '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
      '<w:abstractNum w:abstractNumId="0">' +
      '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="l"/>' +
      '<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/></w:rPr></w:lvl>' +
      '</w:abstractNum>' +
      '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>' +
      '</w:numbering>'
    const bytes = await buildDocx({
      bodyXml:
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>项</w:t></w:r></w:p>',
      numberingXml,
    })
    const doc = await parseDocx(bytes)
    expect(doc.numbering.get('1')!.levels[0].font).toBe('Wingdings')
  })
})
