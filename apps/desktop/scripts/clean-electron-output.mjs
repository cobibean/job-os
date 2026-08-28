import { rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const allowed = ['main', 'preload', 'shared'].map(name => path.join(desktopRoot, 'dist', name))

for (const target of allowed) {
  const relative = path.relative(desktopRoot, target)
  if (!relative.startsWith(`dist${path.sep}`) || relative.split(path.sep).length !== 2) {
    throw new Error(`Refusing to clean unexpected Electron output: ${target}`)
  }
  await rm(target, { recursive: true, force: true })
}
