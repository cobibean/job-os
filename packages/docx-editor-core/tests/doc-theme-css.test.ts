/**
 * Applying a theme has to change the page, not only the saved file.
 * docThemeCss is generated from live theme state, so the render reflects a pick
 * immediately.
 */
import { describe, expect, it } from 'vitest'
import { docThemeCss } from '../src/doc-style-css'

describe('docThemeCss', () => {
  it('emits body and heading fonts from the theme font pair', () => {
    const css = docThemeCss({ major: 'Trebuchet MS', minor: 'Trebuchet MS' }, null)
    expect(css).toContain('.doc-page {')
    expect(css).toContain('Trebuchet MS')
    expect(css).toContain('.doc-page h1')
  })

  it('emits the accent color for headings and the ribbon accent variable', () => {
    const css = docThemeCss(null, { accent1: '90C226' })
    expect(css).toContain('--theme-accent:#90C226')
    expect(css).toContain('color:#90C226')
  })

  it('is empty without a theme, so document CSS stays authoritative', () => {
    expect(docThemeCss(null, null)).toBe('')
    expect(docThemeCss({ major: '', minor: '' }, {})).toBe('')
  })
})
