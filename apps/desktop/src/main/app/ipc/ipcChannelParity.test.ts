import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from 'vitest'

const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')

async function productionSources(root: string): Promise<string[]> {
  const result: string[] = []
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name)
    if (entry.isDirectory()) result.push(...await productionSources(target))
    else if (/\.(?:ts|cts)$/.test(entry.name) && !entry.name.endsWith('.test.ts')) result.push(await readFile(target, 'utf8'))
  }
  return result
}

function channels(source: string, pattern: RegExp): string[] {
  return [...source.matchAll(pattern)].map(match => match[1] as string)
}

test('preload requests have exactly one main registration with the same kind', async () => {
  const preload = await readFile(path.join(sourceRoot, 'preload/preload.cts'), 'utf8')
  const main = (await productionSources(path.join(sourceRoot, 'main'))).join('\n')
  const invokes = new Set(channels(preload, /ipcRenderer\.invoke\(\s*['"]([^'"]+)['"]/g))
  const synchronous = new Set(channels(preload, /ipcRenderer\.sendSync\(\s*['"]([^'"]+)['"]/g))
  const sends = new Set(channels(preload, /ipcRenderer\.send\(\s*['"]([^'"]+)['"]/g))
  const handles = channels(main, /(?:ipc|ipcMain)\.handle\(\s*['"]([^'"]+)['"]/g)
  const listeners = channels(main, /(?:ipc|ipcMain)\.on\(\s*['"]([^'"]+)['"]/g)

  for (const channel of invokes) expect(handles.filter(value => value === channel), channel).toHaveLength(1)
  for (const channel of [...synchronous, ...sends]) expect(listeners.filter(value => value === channel), channel).toHaveLength(1)
  expect(new Set(handles)).toEqual(invokes)
})
