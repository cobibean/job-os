// @vitest-environment node

import { expect, test } from 'vitest'

import { vi } from 'vitest'

import { applyDenyAllPermissionPolicy, isTrustedRendererUrl } from './security.js'

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

test('managed renderer sessions receive the deny-all permission policy', () => {
  const target = {
    setPermissionCheckHandler: vi.fn(),
    setPermissionRequestHandler: vi.fn()
  }
  applyDenyAllPermissionPolicy(target as never)
  expect(target.setPermissionCheckHandler).toHaveBeenCalledOnce()
  expect(target.setPermissionRequestHandler).toHaveBeenCalledOnce()
  expect(target.setPermissionCheckHandler.mock.calls[0]![0]()).toBe(false)
  const callback = vi.fn()
  target.setPermissionRequestHandler.mock.calls[0]![0](null, 'camera', callback)
  expect(callback).toHaveBeenCalledWith(false)
})
