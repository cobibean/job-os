import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'

const importPattern = /@import\s+(?:url\(\s*)?["']([^"']+)["']\s*\)?\s*;/g

export interface ResolvedStylesheet {
  files: string[]
  source: string
}

export function resolveStylesheetImports(entrypoint: string): ResolvedStylesheet {
  const files: string[] = []
  const resolving = new Set<string>()

  function resolveFile(file: string): string {
    const absoluteFile = resolve(file)
    if (resolving.has(absoluteFile)) {
      throw new Error(`Circular stylesheet import: ${absoluteFile}`)
    }

    resolving.add(absoluteFile)
    const source = readFileSync(absoluteFile, 'utf8')
    const imports = Array.from(source.matchAll(importPattern))

    if (imports.length === 0) {
      files.push(absoluteFile)
      resolving.delete(absoluteFile)
      return source
    }

    const remainder = source.replace(importPattern, '').trim()
    if (remainder !== '') {
      throw new Error(`Stylesheet import manifest contains declarations: ${absoluteFile}`)
    }

    const resolved = imports.map(match => {
      const specifier = match[1]!
      if (!specifier.startsWith('.')) {
        throw new Error(`Stylesheet import must be relative: ${specifier}`)
      }
      return resolveFile(resolve(dirname(absoluteFile), specifier))
    }).join('')

    resolving.delete(absoluteFile)
    return resolved
  }

  return { files, source: resolveFile(entrypoint) }
}
