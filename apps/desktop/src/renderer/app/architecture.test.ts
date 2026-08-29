import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, test } from 'vitest'

const rendererRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const mainRoot = path.resolve(rendererRoot, '..', 'main')

const approvedRootEntries = [
  'README.md',
  'agents',
  'app',
  'browser',
  'career-profile',
  'documents',
  'docx-worker.html',
  'env.d.ts',
  'index.html',
  'installation-profiles',
  'jobs',
  'main.tsx',
  'pagedjs.d.ts',
  'print.html',
  'styles.css',
  'workspace'
].sort()

const featureOwnerNames = [
  'agents',
  'browser',
  'career-profile',
  'documents',
  'installation-profiles',
  'jobs',
  'workspace'
] as const

const workspaceFeatureNames = [
  'agents',
  'browser',
  'career-profile',
  'documents',
  'installation-profiles',
  'jobs'
] as const

const legacyRootBuckets = [
  'agent-avatar',
  'components',
  'diagnostics',
  'document-editor',
  'hooks',
  'onboarding',
  'theme'
] as const

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

function modulePathStem(target: string): string {
  return target.replace(/\.(?:[cm]?[jt]sx?)$/, '')
}

async function forbiddenRelativeImports(
  files: string[],
  isForbidden: (target: string) => boolean
): Promise<Array<{ file: string; specifier: string }>> {
  const violations: Array<{ file: string; specifier: string }> = []

  for (const file of files) {
    const source = await readFile(file, 'utf8')
    for (const specifier of moduleSpecifiers(source)) {
      if (!specifier.startsWith('.')) continue
      const target = path.resolve(path.dirname(file), specifier)
      if (isForbidden(target)) {
        violations.push({
          file: path.relative(rendererRoot, file),
          specifier
        })
      }
    }
  }

  return violations
}

describe('desktop renderer architecture', () => {
  test('keeps the renderer root limited to stable entries and approved owners', async () => {
    expect((await readdir(rendererRoot)).sort()).toEqual(approvedRootEntries)
  })

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

  test('keeps production sources out of legacy root buckets', async () => {
    const files = await productionSourceFiles(rendererRoot)
    const legacyFiles = files
      .map(file => path.relative(rendererRoot, file))
      .filter(file => legacyRootBuckets.includes(file.split(path.sep)[0] as typeof legacyRootBuckets[number]))

    expect(legacyFiles).toEqual([])
  })

  test('prevents feature owners from importing renderer entrypoints or app composition', async () => {
    const forbiddenTargets = [
      path.join(rendererRoot, 'main'),
      path.join(rendererRoot, 'app', 'App'),
      path.join(rendererRoot, 'app', 'WorkbenchApp')
    ]
    const featureFiles = (await Promise.all(
      featureOwnerNames.map(owner => productionSourceFiles(path.join(rendererRoot, owner)))
    )).flat()
    const violations = await forbiddenRelativeImports(featureFiles, target => (
      forbiddenTargets.includes(modulePathStem(target))
    ))

    expect(violations).toEqual([])
  })

  test('keeps workspace production modules free of feature implementations', async () => {
    const featureRoots = workspaceFeatureNames.map(owner => path.join(rendererRoot, owner))
    const workspaceFiles = await productionSourceFiles(path.join(rendererRoot, 'workspace'))
    const violations = await forbiddenRelativeImports(workspaceFiles, target => (
      featureRoots.some(root => isWithin(root, target))
    ))

    expect(violations).toEqual([])
  })

  test('keeps main.tsx as the stable application mount without feature implementation or bridge access', async () => {
    const source = await readFile(path.join(rendererRoot, 'main.tsx'), 'utf8')

    expect(moduleSpecifiers(source)).toEqual([
      'react',
      'react-dom/client',
      './app/App',
      './styles.css',
      '@jobos/docx-editor-core/editor.css'
    ])
    expect(source).not.toMatch(/\b(?:window|globalThis)\s*(?:\.\s*jobos|\[\s*['"]jobos['"]\s*\])/)
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
