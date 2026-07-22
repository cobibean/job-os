// @vitest-environment node

import { expect, test } from 'vitest'

import { safeExternalUrl } from './externalLinks.js'

test('allows only bounded HTTP and HTTPS external links', () => {
  expect(safeExternalUrl('https://example.com/docs')).toBe('https://example.com/docs')
  expect(safeExternalUrl('http://example.com')).toBe('http://example.com/')
  expect(safeExternalUrl('javascript:alert(1)')).toBeNull()
  expect(safeExternalUrl('data:text/html,unsafe')).toBeNull()
  expect(safeExternalUrl('file:///tmp/private')).toBeNull()
  expect(safeExternalUrl('not a URL')).toBeNull()
  expect(safeExternalUrl(`https://example.com/${'a'.repeat(8_192)}`)).toBeNull()
})
