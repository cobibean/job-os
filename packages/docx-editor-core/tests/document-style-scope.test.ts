import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { parseDocx } from '@jobos/docx-engine'
import { afterEach, expect, it } from 'vitest'
import { docStyleCss } from '../src/doc-style-css'

afterEach(() => { document.head.replaceChildren(); document.body.replaceChildren() })

it('applies style-only formatting through editor fallbacks and isolates another document', async () => {
  const bytes = await readFile(resolve(process.cwd(), '../docx-engine/tests/fixtures/(FAKE)-polished-resume.docx'))
  const parsed = await parseDocx(new Uint8Array(bytes))
  parsed.styles.set('Normal', {
    styleId: 'Normal', name: '(FAKE) Normal', type: 'paragraph', isDefault: true,
    display: { fontAscii: 'Verdana', sizeHalfPoints: 28, color: '234567', spaceBeforeTwips: 180, spaceAfterTwips: 240, indentFirstLineTwips: 360 }
  })
  parsed.styles.set('FAKENamed', {
    styleId: 'FAKENamed', name: '(FAKE) Named paragraph', type: 'paragraph',
    display: { color: '678901', spaceAfterTwips: 300 }
  })
  parsed.styles.set('FAKETable', {
    styleId: 'FAKETable', name: '(FAKE) Table', type: 'table', tableDisplay: { fill: 'ABCDEF' }
  })
  const markup = `<div class="tiptap"><p>(FAKE) Body</p><p data-style="FAKENamed">(FAKE) Named</p><table data-tbl-style="FAKETable"><tbody><tr><td>(FAKE) Cell</td></tr></tbody></table></div>`
  const first = document.createElement('article')
  first.className = 'jobos-docx-page doc-page'
  first.dataset.docxStyleScope = 'first'
  first.innerHTML = markup
  const second = first.cloneNode(true) as HTMLElement
  second.dataset.docxStyleScope = 'second'
  document.body.append(first, second)
  const defaults = document.createElement('style')
  defaults.textContent = await readFile(resolve(process.cwd(), 'src/editor.css'), 'utf8')
  const imported = document.createElement('style')
  imported.textContent = docStyleCss(parsed, '.doc-page[data-docx-style-scope="first"]')
  document.head.append(defaults, imported)

  expect(getComputedStyle(first).fontFamily).toContain('Verdana')
  expect(getComputedStyle(first).fontSize).toBe('14pt')
  // The editor inherits the imported page baseline rather than resetting it to Calibri/11pt.
  // jsdom preserves the inherit keyword; Chromium resolves it to the page font.
  expect(getComputedStyle(first.querySelector('.tiptap')!).fontFamily).toBe('inherit')
  expect(getComputedStyle(first.querySelector('p')!).marginTop).toBe('9pt')
  expect(getComputedStyle(first.querySelector('p')!).marginBottom).toBe('12pt')
  expect(getComputedStyle(first.querySelector('p')!).textIndent).toBe('18.0pt')
  expect(getComputedStyle(first.querySelector('[data-style]')!).color).toBe('rgb(103, 137, 1)')
  expect(getComputedStyle(first.querySelector('[data-style]')!).marginBottom).toBe('15pt')
  expect(getComputedStyle(first.querySelector('td')!).backgroundColor).toBe('rgb(171, 205, 239)')
  expect(getComputedStyle(second).fontFamily).toContain('Calibri')
  expect(getComputedStyle(second.querySelector('td')!).backgroundColor).not.toBe('rgb(171, 205, 239)')
})
