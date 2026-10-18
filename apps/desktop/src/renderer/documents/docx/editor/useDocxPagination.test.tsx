import { act, cleanup, renderHook } from '@testing-library/react'
import type { Editor } from '@tiptap/react'
import { parseDocx } from '@jobos/docx-engine'
import { setPageGaps } from '@jobos/docx-editor-core'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useDocxPagination } from './useDocxPagination'

vi.mock('@jobos/docx-editor-core', async importOriginal => ({
  ...await importOriginal<typeof import('@jobos/docx-editor-core')>(),
  setPageGaps: vi.fn()
}))

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.clearAllMocks() })

function rect(top: number, width: number, height: number): DOMRect {
  return { top, bottom: top + height, left: 0, right: width, x: 0, y: top, width, height, toJSON: () => ({}) }
}

describe('DOCX pagination at viewing scales', () => {
  it('does not schedule an obsolete measurement when fonts finish after unmount', async () => {
    const bytes = await readFile(resolve(process.cwd(), '../../packages/docx-engine/tests/fixtures/(FAKE)-polished-resume.docx'))
    const parsed = await parseDocx(new Uint8Array(bytes))
    const originalFonts = Object.getOwnPropertyDescriptor(document, 'fonts')
    let finishFonts!: () => void
    const ready = new Promise<void>(resolve => { finishFonts = resolve })
    Object.defineProperty(document, 'fonts', { configurable: true, value: { ready, addEventListener: vi.fn(), removeEventListener: vi.fn() } })
    vi.useFakeTimers()
    const editor = { view: { dom: document.createElement('div') }, on: vi.fn(), off: vi.fn() } as unknown as Editor
    try {
      const { unmount } = renderHook(() => useDocxPagination(editor, parsed))
      unmount()
      const callsAfterCleanup = vi.mocked(setPageGaps).mock.calls.length
      await act(async () => { finishFonts(); await ready; vi.advanceTimersByTime(200) })
      expect(vi.mocked(setPageGaps).mock.calls).toHaveLength(callsAfterCleanup)
    } finally {
      if (originalFonts) Object.defineProperty(document, 'fonts', originalFonts)
      else Reflect.deleteProperty(document, 'fonts')
    }
  })

  it('keeps mid-paragraph page-break anchors at the same text positions across zoom', async () => {
    const bytes = await readFile(resolve(process.cwd(), '../../packages/docx-engine/tests/fixtures/(FAKE)-polished-resume.docx'))
    const parsed = await parseDocx(new Uint8Array(bytes))
    const pm = document.createElement('div')
    pm.style.width = '600px'
    const paragraph = document.createElement('p')
    paragraph.textContent = '(FAKE)abc '.repeat(120)
    pm.append(paragraph)
    document.body.append(pm)
    let scale = 1
    pm.getBoundingClientRect = paragraph.getBoundingClientRect = () => rect(30, 600 * scale, 2400 * scale)
    const createRange = document.createRange.bind(document)
    vi.spyOn(document, 'createRange').mockImplementation(() => {
      const range = createRange()
      range.getClientRects = () => {
        const wholeText = range.endOffset - range.startOffset > 1
        const lines = wholeText ? Array.from({ length: 120 }, (_, index) => index) : [Math.floor(range.startOffset / 10)]
        return lines.map(line => rect(30 + line * 20 * scale, 10 * scale, 18 * scale)) as unknown as DOMRectList
      }
      return range
    })
    const listeners = new Map<string, () => void>()
    const editor = {
      view: { dom: pm, posAtDOM: (_node: Node, offset: number) => offset + 1 },
      on: (event: string, handler: () => void) => { listeners.set(event, handler) },
      off: (event: string) => { listeners.delete(event) }
    } as unknown as Editor
    const { result, rerender, unmount } = renderHook(({ viewingScale }) => useDocxPagination(editor, parsed, viewingScale), { initialProps: { viewingScale: scale } })
    const expectedCount = result.current
    const positions = () => vi.mocked(setPageGaps).mock.calls.at(-1)?.[1].map(gap => 'pos' in gap ? gap.pos : undefined)
    const expectedPositions = positions()
    expect(expectedCount).toBeGreaterThan(1)
    expect(expectedPositions?.length).toBe(expectedCount - 1)
    expect(expectedPositions?.every(pos => typeof pos === 'number' && pos > 1)).toBe(true)
    vi.useFakeTimers()
    for (const nextScale of [0.6, 1, 1.5, 0.6 * 0.8, 1.5 * 0.8]) {
      scale = nextScale
      rerender({ viewingScale: scale })
      act(() => { listeners.get('update')?.(); vi.advanceTimersByTime(200) })
      expect(result.current).toBe(expectedCount)
      expect(positions()).toEqual(expectedPositions)
    }
    unmount()
    pm.remove()
  })

  it.each([1, 0.8])('keeps page boundaries after zoom, edits and resize at responsive scale %s', async responsiveScale => {
    const bytes = await readFile(resolve(process.cwd(), '../../packages/docx-engine/tests/fixtures/(FAKE)-polished-resume.docx'))
    const parsed = await parseDocx(new Uint8Array(bytes))
    const pm = document.createElement('div')
    pm.style.width = '600px'
    let zoom = 1
    let viewportScale = responsiveScale
    const scale = () => zoom * viewportScale
    pm.getBoundingClientRect = () => rect(30, 600 * scale(), 2000 * scale())
    const blocks: HTMLElement[] = Array.from({ length: 4 }, (_, index) => {
      const block = document.createElement('p')
      block.getBoundingClientRect = () => rect(30 + index * 500 * scale(), 600 * scale(), 500 * scale())
      pm.append(block)
      return block
    })
    document.body.append(pm)
    const listeners = new Map<string, () => void>()
    const editor = {
      view: { dom: pm },
      on: (event: string, handler: () => void) => { listeners.set(event, handler) },
      off: (event: string) => { listeners.delete(event) }
    } as unknown as Editor
    const { result, rerender, unmount } = renderHook(({ viewingZoom }) => useDocxPagination(editor, parsed, viewingZoom), { initialProps: { viewingZoom: zoom } })
    const expectedCount = result.current
    const gapStarts = () => vi.mocked(setPageGaps).mock.calls.at(-1)?.[1].map(gap => 'el' in gap ? blocks.indexOf(gap.el!) : gap.pos)
    const expectedGaps = gapStarts()
    expect(expectedCount).toBe(4)
    expect(expectedGaps).toEqual([1, 2, 3])
    vi.useFakeTimers()

    for (const nextZoom of [0.6, 1, 1.5]) {
      zoom = nextZoom
      rerender({ viewingZoom: zoom })
      expect(result.current).toBe(expectedCount)
      expect(gapStarts()).toEqual(expectedGaps)
      act(() => { listeners.get('update')?.(); vi.advanceTimersByTime(200) })
      expect(result.current).toBe(expectedCount)
      expect(gapStarts()).toEqual(expectedGaps)
    }
    viewportScale = responsiveScale === 1 ? 0.8 : 1
    act(() => { window.dispatchEvent(new Event('resize')); vi.advanceTimersByTime(200) })
    expect(result.current).toBe(expectedCount)
    expect(gapStarts()).toEqual(expectedGaps)
    unmount()
    pm.remove()
  })
})
