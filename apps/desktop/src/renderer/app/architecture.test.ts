import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, test } from 'vitest'

const rendererRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const mainRoot = path.resolve(rendererRoot, '..', 'main')

async function productionSourceFiles(root: string): Promise<string[]> {
  const result: string[] = []

  for (const entry of await readdir(root, { withFileTypes: true })) {
    if (entry.isDirectory() && entry.name === 'dist') continue

    const target = path.join(root, entry.name)
    if (entry.isDirectory()) {
      result.push(...await productionSourceFiles(target))
    } else if (/\.(?:ts|tsx)$/.test(entry.name) && !/\.(?:test|spec)\.(?:ts|tsx)$/.test(entry.name)) {
      result.push(target)
    }
  }

  return result.sort()
}

function moduleSpecifiers(source: string): string[] {
  return Array.from(
    source.matchAll(/(?:from\s*|import\s*\(\s*|import\s*|require\s*\(\s*)['"]([^'"]+)['"]/g),
    match => match[1]!
  )
}

function isWithin(root: string, target: string): boolean {
  const relative = path.relative(root, target)
  return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`))
}

describe('desktop renderer architecture', () => {
  test('recursively inventories production sources without imports from Electron main', async () => {
    const files = await productionSourceFiles(rendererRoot)

    expect(files).toContain(path.join(rendererRoot, 'main.tsx'))
    for (const file of files) {
      const source = await readFile(file, 'utf8')
      const mainImports = moduleSpecifiers(source).filter(specifier => {
        if (!specifier.startsWith('.')) return false
        return isWithin(mainRoot, path.resolve(path.dirname(file), specifier))
      })

      expect(mainImports, file).toEqual([])
    }
  })

  test('keeps main.tsx as the stable application mount', async () => {
    const source = await readFile(path.join(rendererRoot, 'main.tsx'), 'utf8')

    expect(source).toMatch(/document\.getElementById\(['"]root['"]\)/)
    expect(source).toMatch(/createRoot\(root\)\.render\(/)
    expect(source).toMatch(/<StrictMode>\s*<App\s*\/>\s*<\/StrictMode>/)
  })

  test('keeps all renderer HTML entrypoints at the root', async () => {
    const rootEntries = await readdir(rendererRoot)

    expect(rootEntries).toEqual(expect.arrayContaining([
      'index.html',
      'print.html',
      'docx-worker.html'
    ]))
  })
})
