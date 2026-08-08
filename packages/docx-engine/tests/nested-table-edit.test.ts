import { describe, expect, it } from 'vitest'
import { generateTableModelXml, parseDocx, patchTableCellTexts, saveDocx } from '../src/index'
import { buildDocx, NESTED_TABLE_XML } from './helpers/build-docx'

describe('nested table editing (surgical text patch + regeneration)', () => {
  it('patches nested cell texts and keeps the outer cell bytes', () => {
    const out = patchTableCellTexts(NESTED_TABLE_XML, [
      [{ nested: [[[['EditedInnerA'], null]]] }, null],
    ])
    expect(out).toContain('EditedInnerA')
    expect(out).toContain('<w:t>InnerB</w:t>')
    expect(out).toContain('<w:t>Outer</w:t>')
    expect(out).toContain('<w:t>RightCell</w:t>')
    // outer structure is not rearranged
    expect(out.indexOf('<w:tbl>')).toBe(0)
  })

  it('plain-array cell patches still skip cells containing nested tables', () => {
    const out = patchTableCellTexts(NESTED_TABLE_XML, [[['ShouldNotApply'], null]])
    expect(out).toBe(NESTED_TABLE_XML)
  })

  it('regenerating a table model keeps nested tables (structure edits no longer drop them)', async () => {
    const doc = await parseDocx(await buildDocx({ bodyXml: NESTED_TABLE_XML }))
    const model = doc.blocks[0].table!
    const xml = generateTableModelXml(model, doc.blocks[0].originalXml ?? undefined)
    expect(xml).toContain('InnerA')
    expect(xml).toContain('InnerB')
    // nested table is followed by an empty paragraph (OOXML: tc must end with w:p)
    expect(/<\/w:tbl><w:p\/><\/w:tc>/.test(xml)).toBe(true)
    // reparsing still yields the nested structure
    const saved = await saveDocx(doc, [{ kind: 'xml', xml }])
    const reparsed = await parseDocx(saved)
    const nested = reparsed.blocks[0].table!.rows[0][0].nestedTables
    expect(nested?.[0]?.rows[0].map((c) => c.paras[0])).toEqual(['InnerA', 'InnerB'])
  })
})
