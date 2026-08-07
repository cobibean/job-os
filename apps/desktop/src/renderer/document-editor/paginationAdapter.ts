import type { AnyExtension, Editor } from '@tiptap/core'
import { PaginationPlus, type PaginationPlusOptions } from 'tiptap-pagination-plus'

import type { DocumentSettings } from '../../shared/editableDocuments.js'

interface PaginationChain {
  updatePageWidth: (value: number) => PaginationChain
  updatePageHeight: (value: number) => PaginationChain
  updatePageGap: (value: number) => PaginationChain
  updateMargins: (value: { top: number; bottom: number; left: number; right: number }) => PaginationChain
  updateHeaderContent: (left: string, right: string) => PaginationChain
  updateFooterContent: (left: string, right: string) => PaginationChain
  run: () => boolean
}

const PIXELS_PER_INCH = 96
const PAGE_PIXELS = {
  letter: { width: 8.5 * PIXELS_PER_INCH, height: 11 * PIXELS_PER_INCH },
  a4: { width: 8.2677 * PIXELS_PER_INCH, height: 11.6929 * PIXELS_PER_INCH }
} as const

export function escapePaginationText(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function edgeText(center: string, edge: string): string {
  return [center, edge].filter(Boolean).map(escapePaginationText).join(' · ')
}

export function paginationOptions(settings: DocumentSettings): Partial<PaginationPlusOptions> {
  const page = PAGE_PIXELS[settings.pageSize]
  const differentFirstPage = settings.header.firstPageDifferent || settings.footer.firstPageDifferent
  const pageNumber = settings.showPageNumbers ? '{page}' : ''
  return {
    enabled: true,
    pageWidth: page.width,
    pageHeight: page.height,
    pageGap: 28,
    pageGapBorderSize: 1,
    pageGapBorderColor: '#c9cdd3',
    pageBreakBackground: '#eef0f3',
    marginTop: settings.marginsInches.top * PIXELS_PER_INCH,
    marginBottom: settings.marginsInches.bottom * PIXELS_PER_INCH,
    marginLeft: settings.marginsInches.left * PIXELS_PER_INCH,
    marginRight: settings.marginsInches.right * PIXELS_PER_INCH,
    contentMarginTop: 0,
    contentMarginBottom: 0,
    headerLeft: escapePaginationText(settings.header.left),
    headerRight: edgeText(settings.header.center, settings.header.right),
    footerLeft: escapePaginationText(settings.footer.left),
    footerRight: edgeText(settings.footer.center, [settings.footer.right, pageNumber].filter(Boolean).join(' ')),
    customHeader: differentFirstPage ? { 1: { headerLeft: '', headerRight: '' } } : {},
    customFooter: differentFirstPage ? { 1: { footerLeft: '', footerRight: '' } } : {}
  }
}

export function createPaginationExtension(settings: DocumentSettings): AnyExtension {
  // The package bundles a second Tiptap type identity. Keep that mismatch at this one adapter boundary.
  return PaginationPlus.configure(paginationOptions(settings)) as unknown as AnyExtension
}

export function applyPaginationSettings(editor: Editor, settings: DocumentSettings): boolean {
  const options = paginationOptions(settings)
  try {
    const chain = editor.chain() as unknown as PaginationChain
    return chain
      .updatePageWidth(options.pageWidth!)
      .updatePageHeight(options.pageHeight!)
      .updatePageGap(options.pageGap!)
      .updateMargins({
        top: options.marginTop!,
        bottom: options.marginBottom!,
        left: options.marginLeft!,
        right: options.marginRight!
      })
      .updateHeaderContent(options.headerLeft!, options.headerRight!)
      .updateFooterContent(options.footerLeft!, options.footerRight!)
      .run()
  } catch {
    const commands = editor.commands as unknown as { disablePagination: () => boolean }
    commands.disablePagination()
    return false
  }
}
