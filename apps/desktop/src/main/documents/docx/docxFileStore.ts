import { execFile } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { link, mkdir, open, readFile, readdir, stat, unlink } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { parseDocx } from '@jobos/docx-engine'

import type { DocxRecoveryEntry } from '../../../shared/docxDocuments.js'

export class DocxExternalChangeError extends Error {
  constructor() {
    super('This DOCX changed outside JobOS. Reload it or save your version as a copy.')
    this.name = 'DocxExternalChangeError'
  }
}

export function sha256(bytes: Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex')
}

export interface ReadDocxResult {
  bytes: Uint8Array
  sha256: string
  byteLength: number
  modifiedAtMs: number
  mode: number
}

type AtomicReplace = (canonicalPath: string, temporaryPath: string, expectedSha256: string) => Promise<boolean>

function defaultAtomicReplaceHelperPath(): string {
  if (!process.defaultApp && process.resourcesPath) {
    return path.join(process.resourcesPath, 'jobos-docx-atomic-replace')
  }
  return fileURLToPath(new URL('../../../../build/jobos-docx-atomic-replace', import.meta.url))
}

function nativeAtomicReplace(
  canonicalPath: string,
  temporaryPath: string,
  expectedSha256: string,
  helperPath = defaultAtomicReplaceHelperPath()
): Promise<boolean> {
  return new Promise((resolve, reject) => {
    execFile(
      helperPath,
      [canonicalPath, temporaryPath, expectedSha256],
      { encoding: 'utf8', maxBuffer: 8_192, timeout: 10_000 },
      error => {
        if (!error) resolve(true)
        else if ((error as Error & { code?: number }).code === 3) resolve(false)
        else reject(error)
      }
    )
  })
}

function nativeSyncParent(filePath: string, helperPath = defaultAtomicReplaceHelperPath()): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile(helperPath, [filePath], { encoding: 'utf8', maxBuffer: 8_192, timeout: 10_000 }, error => {
      if (error) reject(error)
      else resolve()
    })
  })
}

interface DocxFileStoreOptions {
  recoveryRoot: string
  denyRoots?: string[]
  keepRecoveries?: number
  atomicReplace?: AtomicReplace
  atomicReplaceHelperPath?: string
  syncCanonicalDirectory?: (filePath: string) => Promise<void>
  beforeAtomicReplace?: () => Promise<void>
}

export class DocxFileStore {
  readonly #recoveryRoot: string
  readonly #denyRoots: string[]
  readonly #keepRecoveries: number
  readonly #atomicReplace: AtomicReplace
  readonly #atomicReplaceSynchronizesDirectory: boolean
  readonly #syncCanonicalDirectory: NonNullable<DocxFileStoreOptions['syncCanonicalDirectory']>
  readonly #beforeAtomicReplace?: () => Promise<void>
  readonly #queues = new Map<string, Promise<unknown>>()

  constructor(options: DocxFileStoreOptions) {
    this.#recoveryRoot = path.resolve(options.recoveryRoot)
    this.#denyRoots = (options.denyRoots ?? []).map(root => path.resolve(root))
    this.#keepRecoveries = options.keepRecoveries ?? 20
    const helperPath = options.atomicReplaceHelperPath ?? defaultAtomicReplaceHelperPath()
    this.#atomicReplace = options.atomicReplace ?? ((canonicalPath, temporaryPath, expectedSha256) => (
      nativeAtomicReplace(canonicalPath, temporaryPath, expectedSha256, helperPath)
    ))
    this.#atomicReplaceSynchronizesDirectory = !options.atomicReplace
    this.#syncCanonicalDirectory = options.syncCanonicalDirectory ?? (filePath => nativeSyncParent(filePath, helperPath))
    this.#beforeAtomicReplace = options.beforeAtomicReplace
  }

  assertWritablePath(filePath: string): void {
    const resolved = path.resolve(filePath)
    if (path.extname(resolved).toLowerCase() !== '.docx') throw new Error('Selected file must be a DOCX')
    if (this.#denyRoots.some(root => resolved === root || resolved.startsWith(`${root}${path.sep}`))) {
      throw new Error('This generated artifact is immutable. Choose the original DOCX or Save a Copy.')
    }
  }

  async read(filePath: string): Promise<ReadDocxResult> {
    this.assertWritablePath(filePath)
    const info = await stat(filePath, { bigint: false })
    if (!info.isFile()) throw new Error('The bound DOCX is not a regular file')
    const bytes = new Uint8Array(await readFile(filePath))
    await this.#validate(bytes)
    return { bytes, sha256: sha256(bytes), byteLength: bytes.byteLength, modifiedAtMs: info.mtimeMs, mode: info.mode }
  }

  async writeNew(filePath: string, bytes: Uint8Array): Promise<ReadDocxResult> {
    this.assertWritablePath(filePath)
    await this.#validate(bytes)
    return this.#serialize(filePath, async () => {
      const temporary = this.#temporarySibling(filePath)
      const handle = await open(temporary, 'wx', 0o600)
      try {
        await handle.writeFile(bytes)
        await handle.sync()
      } catch (error) {
        await unlink(temporary).catch(() => undefined)
        throw error
      } finally {
        await handle.close()
      }
      try {
        await link(temporary, filePath)
        await unlink(temporary)
      } catch (error) {
        await unlink(temporary).catch(() => undefined)
        throw error
      }
      await this.#syncCanonicalDirectory(filePath)
      return this.read(filePath)
    })
  }

  async replace(
    bindingId: string,
    filePath: string,
    expectedSha256: string,
    bytes: Uint8Array,
    reason: DocxRecoveryEntry['reason'] = 'autosave'
  ): Promise<{ file: ReadDocxResult; recovery: DocxRecoveryEntry }> {
    this.assertWritablePath(filePath)
    await this.#validate(bytes)
    return this.#serialize(filePath, async () => {
      const current = await this.read(filePath)
      if (current.sha256 !== expectedSha256) throw new DocxExternalChangeError()
      const recovery = await this.createRecovery(bindingId, filePath, current.bytes, reason)
      const temporary = this.#temporarySibling(filePath)
      const handle = await open(temporary, 'wx', current.mode & 0o777)
      try {
        await handle.writeFile(bytes)
        await handle.sync()
      } catch (error) {
        await unlink(temporary).catch(() => undefined)
        throw error
      } finally {
        await handle.close()
      }
      try {
        await this.#beforeAtomicReplace?.()
        const replaced = await this.#atomicReplace(filePath, temporary, expectedSha256)
        if (!replaced) throw new DocxExternalChangeError()
        if (!this.#atomicReplaceSynchronizesDirectory) await this.#syncCanonicalDirectory(filePath)
        const saved = await this.read(filePath)
        if (saved.sha256 !== sha256(bytes)) throw new DocxExternalChangeError()
        return { file: saved, recovery }
      } finally {
        await unlink(temporary).catch(() => undefined)
      }
    })
  }

  async createRecovery(
    bindingId: string,
    sourcePath: string,
    bytes: Uint8Array,
    reason: DocxRecoveryEntry['reason']
  ): Promise<DocxRecoveryEntry> {
    const directory = path.join(this.#recoveryRoot, bindingId)
    await mkdir(directory, { recursive: true, mode: 0o700 })
    const recoveryId = `recovery_${Date.now()}_${randomUUID().slice(0, 8)}`
    const filename = `${recoveryId}.docx`
    const target = path.join(directory, filename)
    const handle = await open(target, 'wx', 0o600)
    try {
      await handle.writeFile(bytes)
      await handle.sync()
    } finally {
      await handle.close()
    }
    const entry: DocxRecoveryEntry = {
      recoveryId,
      bindingId,
      filename: path.basename(sourcePath),
      sha256: sha256(bytes),
      byteLength: bytes.byteLength,
      reason,
      createdAt: new Date().toISOString()
    }
    await open(`${target}.json`, 'wx', 0o600).then(async metadata => {
      try { await metadata.writeFile(`${JSON.stringify(entry)}\n`, 'utf8'); await metadata.sync() } finally { await metadata.close() }
    })
    await this.#syncDirectory(directory)
    await this.#prune(directory)
    return entry
  }

  async listRecoveries(bindingId: string): Promise<DocxRecoveryEntry[]> {
    const directory = path.join(this.#recoveryRoot, bindingId)
    try {
      const names = await readdir(directory)
      const entries = await Promise.all(names.filter(name => name.endsWith('.docx.json')).map(async name => (
        JSON.parse(await readFile(path.join(directory, name), 'utf8')) as DocxRecoveryEntry
      )))
      return entries.sort((left, right) => right.createdAt.localeCompare(left.createdAt))
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []
      throw error
    }
  }

  async recoveryBytes(bindingId: string, recoveryId: string): Promise<Uint8Array> {
    if (!/^recovery_[A-Za-z0-9_-]+$/.test(recoveryId)) throw new Error('Invalid recovery')
    return new Uint8Array(await readFile(path.join(this.#recoveryRoot, bindingId, `${recoveryId}.docx`)))
  }

  async #prune(directory: string): Promise<void> {
    const names = (await readdir(directory)).filter(name => name.endsWith('.docx.json')).sort().reverse()
    for (const metadata of names.slice(this.#keepRecoveries)) {
      const stem = metadata.slice(0, -5)
      await Promise.all([
        unlink(path.join(directory, metadata)).catch(() => undefined),
        unlink(path.join(directory, stem)).catch(() => undefined)
      ])
    }
  }

  async #syncDirectory(directory: string): Promise<void> {
    const handle = await open(directory, 'r')
    try {
      await handle.sync()
    } finally {
      await handle.close()
    }
  }

  async #validate(bytes: Uint8Array): Promise<void> {
    if (bytes.byteLength < 100) throw new Error('Generated DOCX is empty or invalid')
    const parsed = await parseDocx(bytes)
    if (!parsed.blocks.length) throw new Error('Generated DOCX has no document body')
  }

  #temporarySibling(filePath: string): string {
    return path.join(path.dirname(filePath), `.${path.basename(filePath)}.jobos-${process.pid}-${randomUUID()}.tmp`)
  }

  async #serialize<T>(filePath: string, action: () => Promise<T>): Promise<T> {
    const key = path.resolve(filePath)
    const previous = this.#queues.get(key) ?? Promise.resolve()
    const current = previous.catch(() => undefined).then(action)
    this.#queues.set(key, current)
    try {
      return await current
    } finally {
      if (this.#queues.get(key) === current) this.#queues.delete(key)
    }
  }
}
