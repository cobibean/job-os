import path from 'node:path'
import { fileURLToPath } from 'node:url'

export interface DesktopPaths {
  mainRoot: string
  rendererRoot: string
  preloadPath: string
  sourceRoot: string
}

export function resolveDesktopPaths(moduleUrl = import.meta.url): DesktopPaths {
  const runtimeDirectory = path.dirname(fileURLToPath(moduleUrl))
  const mainRoot = path.resolve(runtimeDirectory, '../..')
  return {
    mainRoot,
    rendererRoot: path.resolve(mainRoot, '../renderer'),
    preloadPath: path.resolve(mainRoot, '../preload/preload.cjs'),
    sourceRoot: path.resolve(mainRoot, '../../../..')
  }
}
