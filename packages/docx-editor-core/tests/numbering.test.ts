import { Editor } from '@tiptap/core'
import { describe, expect, it } from 'vitest'
import { parseDocx, saveDocx, type NumberingDef } from '@jobos/docx-engine'
import { buildDocx } from '../../../packages/docx-engine/tests/helpers/build-docx'
import { blocksToPmDoc } from '../src/editor/convert'
import { editorExtensions } from '../src/editor/extensions'
import { computeListMarkers, formatNumber } from '../src/editor/numbering'

function def(
  partial: Partial<NumberingDef> & Pick<NumberingDef, 'numId' | 'levels'>,
): NumberingDef {
  return { abstractNumId: partial.numId, startOverrides: {}, ...partial }
}

const DECIMAL_3LVL = def({
  numId: '1',
  levels: {
    0: { numFmt: 'decimal', lvlText: '%1.', start: 1 },
    1: { numFmt: 'decimal', lvlText: '%1.%2', start: 1 },
    2: { numFmt: 'lowerLetter', lvlText: '%3)', start: 1 },
  },
})

const defs = (...list: NumberingDef[]) => new Map(list.map((d) => [d.numId, d]))

describe('formatNumber', () => {
  it('covers Word number formats', () => {
    expect(formatNumber(3, 'decimal')).toBe('3')
    expect(formatNumber(3, 'decimalZero')).toBe('03')
    expect(formatNumber(27, 'lowerLetter')).toBe('aa')
    expect(formatNumber(2, 'upperLetter')).toBe('B')
    expect(formatNumber(4, 'lowerRoman')).toBe('iv')
    expect(formatNumber(1949, 'upperRoman')).toBe('MCMXLIX')
    expect(formatNumber(12, 'chineseCountingThousand')).toBe('十二')
    expect(formatNumber(305, 'chineseCounting')).toBe('三百零五')
    expect(formatNumber(3, 'decimalEnclosedCircle')).toBe('③')
    expect(formatNumber(9, 'none')).toBe('')
  })
})

describe('computeListMarkers', () => {
  it('numbers multilevel lists and resets deeper levels', () => {
    const markers = computeListMarkers(
      [
        { numId: '1', ilvl: 0 },
        { numId: '1', ilvl: 1 },
        { numId: '1', ilvl: 1 },
        { numId: '1', ilvl: 2 },
        { numId: '1', ilvl: 0 },
        { numId: '1', ilvl: 1 },
      ],
      defs(DECIMAL_3LVL),
    )
    expect(markers).toEqual(['1.', '1.1', '1.2', 'a)', '2.', '2.1'])
  })

  it('continues numbering across interleaved plain paragraphs', () => {
    const markers = computeListMarkers(
      [
        { numId: '1', ilvl: 0 },
        { numId: '1', ilvl: 0 },
      ],
      defs(DECIMAL_3LVL),
    )
    expect(markers).toEqual(['1.', '2.'])
  })

  it('applies start values and startOverride restarts', () => {
    const five = def({
      numId: '5',
      levels: { 0: { numFmt: 'decimal', lvlText: '%1.', start: 5 } },
    })
    const restart = def({
      numId: '6',
      abstractNumId: '5',
      levels: { 0: { numFmt: 'decimal', lvlText: '%1.', start: 5 } },
      startOverrides: { 0: 1 },
    })
    const markers = computeListMarkers(
      [
        { numId: '5', ilvl: 0 },
        { numId: '5', ilvl: 0 },
        { numId: '6', ilvl: 0 },
        { numId: '6', ilvl: 0 },
      ],
      defs(five, restart),
    )
    expect(markers).toEqual(['5.', '6.', '1.', '2.'])
  })

  it('shares counters across numIds of the same abstractNum', () => {
    const a = def({ numId: '1', abstractNumId: '9', levels: DECIMAL_3LVL.levels })
    const b = def({ numId: '2', abstractNumId: '9', levels: DECIMAL_3LVL.levels })
    const markers = computeListMarkers(
      [
        { numId: '1', ilvl: 0 },
        { numId: '2', ilvl: 0 },
      ],
      defs(a, b),
    )
    expect(markers).toEqual(['1.', '2.'])
  })

  it('maps bullet glyphs and falls back per level', () => {
    const bullets = def({
      numId: '3',
      levels: {
        0: { numFmt: 'bullet', lvlText: '', start: 1 },
        1: { numFmt: 'bullet', lvlText: 'o', start: 1 },
        2: { numFmt: 'bullet', lvlText: '', start: 1 },
      },
    })
    const markers = computeListMarkers(
      [
        { numId: '3', ilvl: 0 },
        { numId: '3', ilvl: 1 },
        { numId: '3', ilvl: 2 },
      ],
      defs(bullets),
    )
    expect(markers).toEqual(['•', '◦', '▪'])
  })

  it('returns null for unknown numIds and undefined levels', () => {
    const markers = computeListMarkers(
      [
        { numId: '99', ilvl: 0 },
        { numId: '1', ilvl: 7 },
        { numId: null, ilvl: 0 },
      ],
      defs(DECIMAL_3LVL),
    )
    expect(markers).toEqual([null, null, null])
  })

  it('renders data-marker on list items in the editor', async () => {
    const numberingXml =
      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' +
      '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
      '<w:abstractNum w:abstractNumId="0">' +
      '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>' +
      '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/></w:lvl>' +
      '</w:abstractNum>' +
      '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>' +
      '</w:numbering>'
    const li = (ilvl: number, text: string) =>
      `<w:p><w:pPr><w:numPr><w:ilvl w:val="${ilvl}"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>${text}</w:t></w:r></w:p>`
    const source = await buildDocx({
      bodyXml: li(0, 'level one') + li(1, 'level two') + li(0, 'level one again'),
      numberingXml,
    })
    const parsed = await parseDocx(source)
    const editor = new Editor({
      element: document.createElement('div'),
      extensions: editorExtensions,
    })
    editor.storage.listNumbering.defs = parsed.numbering
    editor.commands.setContent(blocksToPmDoc(parsed.blocks) as never)
    const markers = Array.from(editor.view.dom.querySelectorAll('.doc-li')).map((el) =>
      el.getAttribute('data-marker'),
    )
    expect(markers).toEqual(['1.', '1.1', '2.'])
    editor.destroy()
  })

  it('decodes decimal and hexadecimal numeric references without changing source OOXML', async () => {
    const numberingXml =
      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
      '<w:abstractNum w:abstractNumId="0">' +
      '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#61623;"/></w:lvl>' +
      '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#xF0A7;"/></w:lvl>' +
      '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>' +
      '</w:numbering>'
    const source = await buildDocx({
      bodyXml:
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Item</w:t></w:r></w:p>',
      numberingXml,
    })
    const parsed = await parseDocx(source)
    const parsedDef = parsed.numbering.get('1')!

    expect(parsedDef.levels[0].lvlText).toBe(String.fromCodePoint(61623))
    expect(parsedDef.levels[1].lvlText).toBe(String.fromCodePoint(0xf0a7))
    expect(
      computeListMarkers(
        [
          { numId: '1', ilvl: 0 },
          { numId: '1', ilvl: 1 },
        ],
        parsed.numbering,
      ),
    ).toEqual(['•', '▪'])

    const originals = parsed.blocks
      .filter((block) => !block.hidden && block.docxIndex !== null)
      .map((block) => ({ kind: 'original' as const, docxIndex: block.docxIndex! }))
    expect(await saveDocx(parsed, originals)).toEqual(source)
  })

  it('Chinese numbering and bracketed/circled lvlText', () => {
    const cn = def({
      numId: '8',
      levels: { 0: { numFmt: 'chineseCountingThousand', lvlText: '%1、', start: 1 } },
    })
    const markers = computeListMarkers(
      Array.from({ length: 11 }, () => ({ numId: '8', ilvl: 0 })),
      defs(cn),
    )
    expect(markers[0]).toBe('一、')
    expect(markers[9]).toBe('十、')
    expect(markers[10]).toBe('十一、')
  })

  it('symbol-font bullets decode by font; undecodable ones fall back to the default symbol', () => {
    const mk = (numId: string, lvlText: string, font?: string) =>
      def({
        numId,
        levels: { 0: { numFmt: 'bullet', lvlText, start: 1, ...(font ? { font } : {}) } },
      })
    const markers = computeListMarkers(
      [
        { numId: '1', ilvl: 0 }, // Wingdings 'l' = ●
        { numId: '2', ilvl: 0 }, // Wingdings PUA F0A7 = ▪
        { numId: '3', ilvl: 0 }, // Webdings has no mapping table → default
        { numId: '4', ilvl: 0 }, // glyph not in the Wingdings table → default
        { numId: '5', ilvl: 0 }, // PUA without a font → legacy font-agnostic mapping
      ],
      defs(
        mk('1', 'l', 'Wingdings'),
        mk('2', '', 'Wingdings'),
        mk('3', '', 'Webdings'),
        mk('4', 'e', 'Wingdings'),
        mk('5', ''),
      ),
    )
    expect(markers).toEqual(['●', '▪', '•', '•', '•'])
  })
})
