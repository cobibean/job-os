import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parseDocx } from '@jobos/docx-engine'
import { applyStructuredOperations, buildPatchedDocx, parseDocxForEditing, serializeDocumentContext } from '../src/index'

async function resume(): Promise<Uint8Array> {
  const path = resolve(process.cwd(), '../docx-engine/tests/fixtures/(FAKE)-polished-resume.docx')
  return new Uint8Array(await readFile(path))
}

describe('JobOS editor facade', () => {
  it('returns original bytes when the ProseMirror document is unchanged', async () => {
    const bytes = await resume()
    const document = await parseDocxForEditing(bytes)
    const saved = await buildPatchedDocx(document, document.pmDoc)
    expect(saved).toBe(bytes)
  })

  it('persists paragraph alignment into the canonical OOXML', async () => {
    const document = await parseDocxForEditing(await resume())
    const aligned = structuredClone(document.pmDoc)
    const paragraph = aligned.content?.find(node => (
      node.type === 'docParagraph' || node.type === 'docHeading' || node.type === 'docListItem'
    ))
    if (!paragraph) throw new Error('(FAKE) editable paragraph fixture missing')
    const docxIndex = paragraph.attrs?.docxIndex
    paragraph.attrs = { ...paragraph.attrs, align: 'center' }

    const saved = await buildPatchedDocx(document, aligned, '2026-08-08T00:00:00.000Z')
    const reparsed = await parseDocx(saved)
    const persisted = reparsed.blocks.find(block => block.docxIndex === docxIndex)

    expect(persisted?.format?.align).toBe('center')
  })

  it('reopens justified OOXML with the Justify state intact', async () => {
    const document = await parseDocxForEditing(await resume())
    const justified = structuredClone(document.pmDoc)
    const paragraph = justified.content?.find(node => (
      node.type === 'docParagraph' || node.type === 'docHeading' || node.type === 'docListItem'
    ))
    if (!paragraph) throw new Error('(FAKE) editable paragraph fixture missing')
    const docxIndex = paragraph.attrs?.docxIndex
    paragraph.attrs = { ...paragraph.attrs, align: 'justify' }

    const saved = await buildPatchedDocx(document, justified, '2026-08-08T00:00:00.000Z')
    const canonical = await parseDocx(saved)
    expect(canonical.blocks.find(block => block.docxIndex === docxIndex)?.format?.align).toBe('justify')

    const reopened = await parseDocxForEditing(saved)
    expect(reopened.pmDoc.content?.find(node => node.attrs?.docxIndex === docxIndex)?.attrs?.align).toBe('justify')
  })

  it('applies expected-text checked edits and saves them into canonical OOXML', async () => {
    const document = await parseDocxForEditing(await resume())
    const context = serializeDocumentContext(document.pmDoc)
    const block = context.blocks.find(item => item.text.length > 0 && !item.protected)!
    const marker = ' (FAKE) structured JobOS edit'
    const result = applyStructuredOperations(document.pmDoc, [{
      type: 'replace_block_text',
      blockId: block.id,
      expectedCurrentText: block.text,
      text: `${block.text}${marker}`,
    }])
    const saved = await buildPatchedDocx(document, result.document, '2026-08-08T00:00:00.000Z')
    const reparsed = await parseDocx(saved)
    expect(reparsed.blocks.flatMap(item => item.runs ?? []).map(run => run.text).join('')).toContain(marker)
  })

  it('rejects stale and protected structured edits', async () => {
    const document = await parseDocxForEditing(await resume())
    const block = serializeDocumentContext(document.pmDoc).blocks.find(item => item.text.length > 0 && !item.protected)!
    expect(() => applyStructuredOperations(document.pmDoc, [{ type: 'replace_block_text', blockId: block.id, expectedCurrentText: 'stale', text: 'nope' }])).toThrow(/document_stale_text/)
  })
})
