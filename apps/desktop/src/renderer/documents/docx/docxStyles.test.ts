import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

it('uses defined JobOS theme tokens throughout DOCX editor states', () => {
  const foundation = readFileSync(resolve(process.cwd(), 'src/renderer/app/styles/foundation.css'), 'utf8')
  const stylesheet = readFileSync(resolve(process.cwd(), 'src/renderer/documents/docx/docx.css'), 'utf8')
  const defined = new Set([...foundation.matchAll(/(--[\w-]+)\s*:/g)].map(match => match[1]))
  // The viewing zoom is supplied by the document editor, not by the active theme.
  defined.add('--docx-zoom')
  const unresolved = [...stylesheet.matchAll(/var\((--[\w-]+)/g)].map(match => match[1]).filter(token => !defined.has(token))
  expect([...new Set(unresolved)]).toEqual([])
})
