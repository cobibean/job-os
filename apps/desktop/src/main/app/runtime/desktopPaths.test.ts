import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { expect, test } from 'vitest'

import { resolveDesktopPaths } from './desktopPaths.js'

test('resolves source Electron paths from the runtime module depth', () => {
  const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..')
  const paths = resolveDesktopPaths(pathToFileURL(path.join(desktopRoot, 'src/main/app/runtime/desktopPaths.ts')).href)
  expect(paths.mainRoot).toBe(path.join(desktopRoot, 'src/main'))
  expect(paths.rendererRoot).toBe(path.join(desktopRoot, 'src/renderer'))
  expect(paths.preloadPath).toBe(path.join(desktopRoot, 'src/preload/preload.cjs'))
  expect(paths.sourceRoot).toBe(path.resolve(desktopRoot, '../..'))
})

test('resolves compiled Electron paths without changing the stable entrypoint', () => {
  const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..')
  const paths = resolveDesktopPaths(pathToFileURL(path.join(desktopRoot, 'dist/main/app/runtime/desktopPaths.js')).href)
  expect(paths.mainRoot).toBe(path.join(desktopRoot, 'dist/main'))
  expect(paths.rendererRoot).toBe(path.join(desktopRoot, 'dist/renderer'))
  expect(paths.preloadPath).toBe(path.join(desktopRoot, 'dist/preload/preload.cjs'))
  expect(paths.sourceRoot).toBe(path.resolve(desktopRoot, '../..'))
})
