import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, test } from 'vitest'

const mainRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

async function sourceFiles(root: string): Promise<string[]> {
  const result: string[] = []
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name)
    if (entry.isDirectory()) result.push(...await sourceFiles(target))
    else if (/\.(?:ts|tsx|cts)$/.test(entry.name) && !entry.name.endsWith('.test.ts')) result.push(target)
  }
  return result
}

describe('Electron main architecture', () => {
  test('allows only the stable entrypoint, README, and ownership directories at the root', async () => {
    expect((await readdir(mainRoot)).sort()).toEqual([
      'README.md', 'agents', 'app', 'browser', 'career-profile', 'documents',
      'installation-profiles', 'jobs', 'main.ts', 'workspace'
    ])
  })

  test('keeps feature modules independent of the entrypoint and bootstrap', async () => {
    for (const owner of ['agents', 'browser', 'career-profile', 'documents', 'installation-profiles', 'jobs', 'workspace']) {
      for (const file of await sourceFiles(path.join(mainRoot, owner))) {
        const source = await readFile(file, 'utf8')
        expect(source, file).not.toMatch(/from ['"][^'"]*(?:app\/bootstrap|main)\.js['"]/)
      }
    }
  })

  test('keeps the root entrypoint free of feature IPC and implementation logic', async () => {
    const source = await readFile(path.join(mainRoot, 'main.ts'), 'utf8')
    expect(source).not.toContain('jobos:')
    expect(source).not.toMatch(/register[A-Z].*Ipc/)
    expect(source).toContain("from './app/bootstrap.js'")
  })
})
