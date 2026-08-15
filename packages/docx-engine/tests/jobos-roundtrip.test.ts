// This file is part of JobOS's modified GenOffice-derived package; see this package's UPSTREAM.md.
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import { parseDocx, saveDocx, type Block, type GeneratedBlock, type SaveBlock } from '../src/index'
import { inventoryDocx } from './helpers/package-diff'

const fixtures = [
  '(FAKE)-polished-resume.docx',
  '(FAKE)-cover-letter.docx',
  '(FAKE)-references.docx',
  '(FAKE)-protected-constructs.docx',
]

async function fixture(name: string): Promise<Uint8Array> {
  return new Uint8Array(await readFile(fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url))))
}

function originals(blocks: Block[]): SaveBlock[] {
  return blocks.filter(block => !block.hidden).map(block => ({ kind: 'original', docxIndex: block.docxIndex! }))
}

function generatedTextBlock(block: Block, marker: string): GeneratedBlock {
  if (!['paragraph', 'heading', 'listItem'].includes(block.type) || !block.runs?.length) {
    throw new Error('Fixture had no editable paragraph')
  }
  return {
    type: block.type as GeneratedBlock['type'],
    level: block.level,
    styleId: block.styleId,
    list: block.list,
    format: block.format,
    rawPPr: block.rawPPr,
    bookmarks: block.bookmarks,
    hiddenBookmarks: block.hiddenBookmarks,
    commentStarts: block.commentStarts,
    commentEnds: block.commentEnds,
    sdtShell: block.sdtShell,
    runs: block.runs.map((run, index) => index === block.runs!.length - 1 ? { ...run, text: `${run.text}${marker}` } : run),
  }
}

describe('JobOS fake fixture fidelity', () => {
  for (const name of fixtures) {
    it(`${name}: no-op save returns exact source bytes`, async () => {
      const source = await fixture(name)
      const parsed = await parseDocx(source)
      const saved = await saveDocx(parsed, originals(parsed.blocks))
      expect(saved).toBe(source)
    })
  }

  it('edits one resume paragraph without removing or changing unrelated package parts', async () => {
    const source = await fixture('(FAKE)-polished-resume.docx')
    const parsed = await parseDocx(source)
    const editable = parsed.blocks.find(block => !block.hidden && ['paragraph', 'heading', 'listItem'].includes(block.type) && block.runs?.length)
    expect(editable).toBeDefined()

    const marker = ' (FAKE) JobOS OOXML edit'
    const finalBlocks: SaveBlock[] = parsed.blocks.filter(block => !block.hidden).map(block =>
      block === editable
        ? { kind: 'generated', block: generatedTextBlock(block, marker) }
        : { kind: 'original', docxIndex: block.docxIndex! },
    )
    const saved = await saveDocx(parsed, finalBlocks, { savedAt: '2026-08-08T00:00:00.000Z' })
    const before = await inventoryDocx(source)
    const after = await inventoryDocx(saved)

    expect([...after.keys()].sort()).toEqual([...before.keys()].sort())
    for (const [name, part] of before) {
      if (name === 'word/document.xml' || name === 'docProps/core.xml') continue
      expect(after.get(name)?.sha256, name).toBe(part.sha256)
    }

    const reparsed = await parseDocx(saved)
    expect(reparsed.blocks.flatMap(block => block.runs ?? []).map(run => run.text).join('')).toContain(marker)
    for (const block of parsed.blocks) {
      if (block === editable || block.originalXml === null) continue
      expect(new TextDecoder().decode(after.get('word/document.xml')!.bytes)).toContain(block.originalXml)
    }
  })
})
