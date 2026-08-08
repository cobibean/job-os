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
