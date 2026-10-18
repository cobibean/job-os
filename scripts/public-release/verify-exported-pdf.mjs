#!/usr/bin/env node
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

// Resolve the desktop's existing PDF reader, without adding a release-only dependency.
const require = createRequire(new URL('../../apps/desktop/package.json', import.meta.url))
const { getDocument } = await import(pathToFileURL(require.resolve('pdfjs-dist/legacy/build/pdf.mjs')).href)

function argument(name) {
  const index = process.argv.indexOf(name)
  const value = index >= 0 ? process.argv[index + 1] : undefined
  if (!value) throw new Error(`${name} is required`)
  return value
}

function textFragments(node) {
  if (!node || typeof node !== 'object') return []
  if (Array.isArray(node)) return node.flatMap(textFragments)
  return [typeof node.text === 'string' ? node.text : '', ...textFragments(node.content)].filter(text => text.trim())
}

// PDF text items can split at line wraps or formatting changes within a word.
const comparableText = value => value.normalize('NFKC').replace(/\s+/gu, '')

function verifyRunningContent(page, content, settings, number, total) {
  const viewport = page.getViewport({ scale: 1 })
  const margins = settings.margins_inches
  const start = margins.left * 72
  const width = viewport.width - start - margins.right * 72
  const gap = 9 // The document's 12 CSS-pixel column gap, in PDF points.
  const columnWidth = (width - 2 * gap) / 3
  const runs = content.items.filter(item => 'str' in item && item.str.trim())
  for (const kind of ['header', 'footer']) {
    const configuration = settings[kind]
    const band = runs.filter(item => kind === 'header'
      ? item.transform[5] > viewport.height - margins.top * 72
      : item.transform[5] < margins.bottom * 72)
    const hidden = number === 1 && configuration.first_page_different
    for (const [column, key] of ['left', 'center', 'right'].entries()) {
      const expected = comparableText(configuration[key].replaceAll('{page}', String(number)).replaceAll('{pages}', String(total)))
      if (!expected) continue
      const allText = comparableText(band.map(item => item.str).join(' '))
      if (hidden) {
        if (allText.includes(expected)) throw new Error(`Exported PDF unexpectedly includes first-page ${kind}`)
        continue
      }
      const left = start + column * (columnWidth + gap)
      const right = left + columnWidth
      const inside = band.filter(item => item.transform[4] >= left - 1 && item.transform[4] + item.width <= right + 1)
      if (!comparableText(inside.map(item => item.str).join(' ')).includes(expected)) {
        throw new Error(`Exported PDF page ${number} ${kind} ${key} is missing or outside its column`)
      }
      const firstX = Math.min(...inside.map(item => item.transform[4]))
      const lastX = Math.max(...inside.map(item => item.transform[4] + item.width))
      const offset = column === 0 ? firstX - left : column === 2 ? lastX - right : (firstX + lastX - left - right) / 2
      if (Math.abs(offset) > 1.5) throw new Error(`Exported PDF page ${number} ${kind} ${key} is not aligned`)
    }
  }
}

async function verify() {
  const input = argument('--input')
  const pdfPath = argument('--pdf')
  if (!path.isAbsolute(input) || !path.isAbsolute(pdfPath)) throw new Error('PDF verification paths must be absolute')
  const expectedPages = Number(argument('--pages'))
  if (!Number.isSafeInteger(expectedPages) || expectedPages < 1) throw new Error('Expected page count must be a positive integer')
  const canonical = JSON.parse(await readFile(input, 'utf8'))
  const expected = textFragments(canonical.content)
  if (!expected.length) throw new Error('PDF acceptance document must contain expected text')
  const loading = getDocument({ data: new Uint8Array(await readFile(pdfPath)), isEvalSupported: false, useSystemFonts: true })
  const pdf = await loading.promise
  try {
    if (pdf.numPages !== expectedPages) throw new Error(`Exported PDF has ${pdf.numPages} pages; expected ${expectedPages}`)
    const text = []
    for (let number = 1; number <= pdf.numPages; number += 1) {
      const page = await pdf.getPage(number)
      const content = await page.getTextContent()
      verifyRunningContent(page, content, canonical.settings, number, pdf.numPages)
      text.push(...content.items.map(item => 'str' in item ? item.str : ''))
      page.cleanup()
    }
    const actual = comparableText(text.join(' '))
    if (!expected.every(fragment => actual.includes(comparableText(fragment)))) {
      throw new Error('Exported PDF does not contain every expected canonical text fragment')
    }
    process.stdout.write(`${JSON.stringify({ pageCount: pdf.numPages, matchedTextFragments: expected.length })}\n`)
  } finally {
    await loading.destroy()
  }
}

try {
  await verify()
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : 'PDF content verification failed'}\n`)
  process.exitCode = 1
}
