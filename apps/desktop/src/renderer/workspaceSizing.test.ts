import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from 'vitest'

const styles = readFileSync(resolve(process.cwd(), 'src/renderer/styles.css'), 'utf8')

function declarationsFor(selector: string) {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = styles.match(new RegExp(`${escapedSelector}\\s*\\{([^}]*)\\}`))
  expect(match, `Missing CSS rule for ${selector}`).not.toBeNull()
  return match?.[1] ?? ''
}

test('the preserved workbench fills the shared workspace after leaving Browse', () => {
  expect(declarationsFor('.workspace-content')).toMatch(/min-height\s*:\s*0\s*;/)
  expect(declarationsFor('.workbench-layer')).toMatch(/height\s*:\s*100%\s*;/)
  expect(declarationsFor('.workbench-wrap')).toMatch(/flex\s*:\s*1(?:\s+1\s+auto)?\s*;/)
})
