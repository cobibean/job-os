import { describe, expect, it } from 'vitest'

import { defaultDocumentSettings } from '../../shared/editableDocumentSchema'
import { escapePaginationText, paginationOptions } from './paginationAdapter'

describe('JobOS pagination adapter', () => {
  it('maps persisted Letter and A4 settings to bounded plugin pixels', () => {
    const letter = paginationOptions(defaultDocumentSettings())
    expect(letter.pageWidth).toBe(816)
    expect(letter.pageHeight).toBe(1056)
    expect(letter.marginTop).toBe(96)

    const a4Settings = defaultDocumentSettings()
    a4Settings.pageSize = 'a4'
    a4Settings.marginsInches = { top: 0.5, right: 0.75, bottom: 1.25, left: 1.5 }
    const a4 = paginationOptions(a4Settings)
    expect(a4.pageWidth).toBeCloseTo(793.6992)
    expect(a4.pageHeight).toBeCloseTo(1122.5184)
    expect(a4.marginLeft).toBe(144)
  })

  it('escapes every header/footer field before passing it to the HTML-capable plugin', () => {
    const settings = defaultDocumentSettings()
    settings.header.left = '<img src=x onerror=alert(1)>'
    settings.header.center = 'A & B'
    settings.footer.right = '"private"'
    settings.showPageNumbers = true
    const options = paginationOptions(settings)

    expect(options.headerLeft).toBe('&lt;img src=x onerror=alert(1)&gt;')
    expect(options.headerRight).toBe('A &amp; B')
    expect(options.footerRight).toBe('&quot;private&quot; {page}')
    expect(String(options.headerLeft)).not.toContain('<img')
  })

  it('hides both header and footer on page one when first-page-different is enabled', () => {
    const settings = defaultDocumentSettings()
    settings.header.firstPageDifferent = true
    const options = paginationOptions(settings)
    expect(options.customHeader).toEqual({ 1: { headerLeft: '', headerRight: '' } })
    expect(options.customFooter).toEqual({ 1: { footerLeft: '', footerRight: '' } })
  })

  it('escapes quotes and apostrophes in plain pagination text', () => {
    expect(escapePaginationText(`<>&"'`)).toBe('&lt;&gt;&amp;&quot;&#39;')
  })
})
