// @vitest-environment node

import { expect, test } from 'vitest'

import { isTrustedRendererUrl } from './security.js'

test('only the packaged renderer or exact development origin may invoke the preload bridge', () => {
  const policy = {
    developmentOrigin: 'http://127.0.0.1:5173',
    rendererRoot: '/Applications/JobOS.app/Contents/Resources/app/dist/renderer'
  }

  expect(isTrustedRendererUrl('http://127.0.0.1:5173/', policy)).toBe(true)
  expect(
    isTrustedRendererUrl(
      'file:///Applications/JobOS.app/Contents/Resources/app/dist/renderer/index.html',
      policy
    )
  ).toBe(true)
  expect(isTrustedRendererUrl('https://attacker.example/', policy)).toBe(false)
  expect(isTrustedRendererUrl('http://127.0.0.1:5173.attacker.example/', policy)).toBe(false)
  expect(isTrustedRendererUrl('file:///tmp/untrusted.html', policy)).toBe(false)
})
