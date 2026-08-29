import type { CSSProperties } from 'react'
import { useEffect, useMemo, useState } from 'react'
import type { Editor } from '@tiptap/react'
import { readSections, type ParsedDocFull } from '@jobos/docx-engine'
import {
  assignSections,
  effectiveBottomPx,
  effectiveTopPx,
  lineStartAnchor,
  liveSections,
  measureBlocks,
  sectionGeoms,
  sectionPageBox,
  setPageGaps,
  sliceWithLineSplit,
  type PageGapSpec
} from '@jobos/docx-editor-core'

const twipsToPx = (twips: number) => (twips / 1440) * 96

export function useDocxPageStyle(parsed: ParsedDocFull): CSSProperties {
  return useMemo(() => {
    const settings = readSections(parsed)[0]?.settings
    if (!settings) return {}
    const page = sectionPageBox(settings)
    return {
      '--doc-page-width': `${page.width}px`,
      '--doc-page-height': `${page.height}px`,
      '--doc-page-padding-x': `${twipsToPx(settings.marginLeft)}px`,
      '--doc-page-padding-y': `${twipsToPx(settings.marginTop)}px`
    } as CSSProperties
  }, [parsed])
}

export function useDocxPagination(editor: Editor | null, parsed: ParsedDocFull): number {
  const [pageCount, setPageCount] = useState(1)

  useEffect(() => {
    if (!editor) return
    const pm = editor.view.dom as HTMLElement
    let timer: number | null = null

    const measure = () => {
      const allSections = readSections(parsed)
      if (allSections.length === 0) return
      const { blocks, totalHeight } = measureBlocks(pm, pm.getBoundingClientRect().top, 1)
      const sections = liveSections(allSections, blocks)
      assignSections(blocks, sections)
      const slices = sliceWithLineSplit(blocks, sectionGeoms(sections), totalHeight, 1)
      const gaps: PageGapSpec[] = []

      slices.slice(1).forEach((slice, index) => {
        const previousSlice = slices[index]
        const defaultSection = sections[0]
        if (!previousSlice || !defaultSection || slice.start === previousSlice.start) return
        const previousSection = sections[Math.min(previousSlice.section, sections.length - 1)] ?? defaultSection
        const nextSection = sections[Math.min(slice.section, sections.length - 1)] ?? defaultSection
        const previous = previousSection.settings
        const next = nextSection.settings
        const metrics = {
          marginTop: effectiveTopPx(next, 0),
          marginBottom: effectiveBottomPx(previous, 0),
          marginLeft: twipsToPx(next.marginLeft),
          marginRight: twipsToPx(next.marginRight)
        }
        const leading = blocks.find(block => block.el && Math.abs(block.top - slice.start) < 0.75)
        if (leading?.el) {
          gaps.push({ el: leading.el, metrics })
          return
        }
        const split = blocks.find(block => (
          block.el && block.top < slice.start && slice.start < block.top + block.height - 0.5
        ))
        if (!split?.el) return
        const anchor = lineStartAnchor(split.el, slice.start - split.top, 1)
        if (!anchor) return
        try {
          const pos = editor.view.posAtDOM(anchor.node, anchor.charOffset)
          if (pos >= 0) gaps.push({ pos, kind: 'inline', metrics })
        } catch {
          // A transient DOM remount will be measured again on the next frame.
        }
      })

      setPageGaps(editor.view, gaps)
      setPageCount(Math.max(1, slices.length))
    }

    const schedule = () => {
      if (timer !== null) window.clearTimeout(timer)
      timer = window.setTimeout(measure, 180)
    }
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule)
    resize?.observe(pm)
    editor.on('update', schedule)
    const fonts = document.fonts
    fonts?.ready.then(schedule).catch(() => undefined)
    fonts?.addEventListener('loadingdone', schedule)
    measure()

    return () => {
      if (timer !== null) window.clearTimeout(timer)
      resize?.disconnect()
      editor.off('update', schedule)
      fonts?.removeEventListener('loadingdone', schedule)
      setPageGaps(editor.view, [])
    }
  }, [editor, parsed])

  return pageCount
}
