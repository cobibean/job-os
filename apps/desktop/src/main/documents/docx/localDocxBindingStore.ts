import { createHash } from 'node:crypto'
import { chmod, mkdir, open, readFile, rename } from 'node:fs/promises'
import path from 'node:path'

import type { DocxBinding } from '../../../shared/docxDocuments.js'

interface BindingFile {
  schemaVersion: 1
  bindings: DocxBinding[]
}

function hash(value: string): string {
  return createHash('sha256').update(value).digest('hex').slice(0, 24)
}

export function docxBindingId(jobId: string, documentKey: string): string {
  return `docx_${hash(`${jobId}\0${documentKey}`)}`
}

export class LocalDocxBindingStore {
  readonly #filePath: string
  #queue = Promise.resolve()

  constructor(filePath: string) {
    this.#filePath = filePath
  }

  async list(jobId?: string): Promise<DocxBinding[]> {
    const file = await this.#read()
    return file.bindings.filter(binding => !jobId || binding.jobId === jobId)
  }

  async get(bindingId: string): Promise<DocxBinding | null> {
    return (await this.#read()).bindings.find(binding => binding.bindingId === bindingId) ?? null
  }

  async getForJob(jobId: string, documentKey: string): Promise<DocxBinding | null> {
    return (await this.#read()).bindings.find(binding => (
      binding.jobId === jobId && binding.documentKey === documentKey
    )) ?? null
  }

  async put(binding: DocxBinding): Promise<void> {
    await this.#mutate(file => {
      const index = file.bindings.findIndex(item => item.bindingId === binding.bindingId)
      if (index >= 0) file.bindings[index] = binding
      else file.bindings.push(binding)
    })
  }

  async remove(bindingId: string): Promise<void> {
    await this.#mutate(file => {
      file.bindings = file.bindings.filter(binding => binding.bindingId !== bindingId)
    })
  }

  async #read(): Promise<BindingFile> {
    try {
      const parsed = JSON.parse(await readFile(this.#filePath, 'utf8')) as BindingFile
      if (parsed.schemaVersion !== 1 || !Array.isArray(parsed.bindings)) throw new Error('Local DOCX binding registry is invalid')
      return parsed
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return { schemaVersion: 1, bindings: [] }
      throw error
    }
  }

  async #mutate(change: (file: BindingFile) => void): Promise<void> {
    const pending = this.#queue.then(async () => {
      const file = await this.#read()
      change(file)
      await this.#write(file)
    })
    this.#queue = pending.catch(() => undefined)
    await pending
  }

  async #write(file: BindingFile): Promise<void> {
    const directory = path.dirname(this.#filePath)
    await mkdir(directory, { recursive: true, mode: 0o700 })
    await chmod(directory, 0o700).catch(() => undefined)
    const temporaryPath = `${this.#filePath}.${process.pid}.${Date.now()}.tmp`
    const handle = await open(temporaryPath, 'wx', 0o600)
    try {
      await handle.writeFile(`${JSON.stringify(file, null, 2)}\n`, 'utf8')
      await handle.sync()
    } finally {
      await handle.close()
    }
    await rename(temporaryPath, this.#filePath)
    await chmod(this.#filePath, 0o600).catch(() => undefined)
  }
}
