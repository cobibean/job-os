import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from 'vitest'

test('startup resolves profile identity and registers application IPC before creating a window', async () => {
  const source = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), 'bootstrap.ts'), 'utf8')
  const startup = source.slice(source.indexOf('async start(): Promise<void>'))
  expect(startup.indexOf('resolveProfileStorageIdentity')).toBeLessThan(startup.indexOf('registerApplicationIpc'))
  expect(startup.indexOf('registerApplicationIpc')).toBeLessThan(startup.indexOf('createWindow()'))
  const windowAttachment = source.slice(source.indexOf('const attachWindowFeatures'))
  expect(windowAttachment.indexOf('new Promise<void>(resolve => { markBrowserRestored = resolve })'))
    .toBeLessThan(windowAttachment.indexOf('startDesktopCapabilityClient'))
  expect(windowAttachment.indexOf('const startWindowStreams'))
    .toBeLessThan(windowAttachment.indexOf('afterShow: startWindowStreams'))
})
