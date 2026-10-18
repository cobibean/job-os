import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from 'vitest'
import { THEMES } from '../theme/themes'

function source(path: string) { return readFileSync(resolve(process.cwd(), 'src/renderer', path), 'utf8') }
function rule(css: string, selector: string): string {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].find(match => match[1]!.trim().split(/,\s*/).includes(selector))?.[2] ?? ''
}
function contrast(a: string, b: string): number {
  const luminance = (hex: string) => {
    const channels = [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16) / 255).map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
    return channels[0]! * 0.2126 + channels[1]! * 0.7152 + channels[2]! * 0.0722
  }
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (values[0]! + 0.05) / (values[1]! + 0.05)
}

test.each(['.navigator-search', '.browse-search'])('%s identifies keyboard focus beyond the input caret', selector => {
  const focused = rule(source('jobs/jobs.css'), `${selector}:focus-within`)
  expect(focused).toMatch(/outline:\s*[2-9]px\s+solid\s+var\(--[\w-]+\)/)
})

test.each(['.job-location', '.status-heading'])('%s text reaches 4.5 contrast in each theme and state background', selector => {
  const css = source('jobs/jobs.css')
  const colorToken = rule(css, selector).match(/color:\s*var\(--([\w-]+)\)/)?.[1]
  expect(colorToken).toBeTruthy()
  for (const theme of THEMES) for (const mode of ['light', 'dark'] as const) {
    for (const background of ['surface', 'surface-inset', 'surface-selected', 'surface-hover']) {
      const tokens = theme.modes[mode]
      expect(contrast(tokens[colorToken!]!, tokens[background]!), `${theme.id} ${mode} ${selector} on ${background}`).toBeGreaterThanOrEqual(4.5)
    }
  }
})

test('agent session labels depend on available width rather than pixel density', () => {
  expect(source('agents/chat/chat.css')).not.toMatch(/min-resolution|device-pixel-ratio/)
})

test('agent session status colors use defined warning and success tokens', () => {
  const css = source('agents/chat/chat.css')
  const foundation = source('app/styles/foundation.css')
  const defined = new Set([...foundation.matchAll(/(--[\w-]+)\s*:/g)].map(match => match[1]))
  for (const state of ['needs-you', 'done', 'interrupted', 'recovering']) {
    const token = rule(css, `.agent-session-state.${state}`).match(/color:\s*var\((--[\w-]+)\)/)?.[1]
    expect(defined.has(token!), state).toBe(true)
  }
})

test('browser toolbar wraps controls instead of clipping them in allowed narrow panes', () => {
  expect(rule(source('browser/browser.css'), '.browser-toolbar')).toMatch(/flex-wrap:\s*wrap/)
})
