import path from 'node:path'

export type MediaCaptureAction =
  | { kind: 'wait'; selector: string; text?: string; timeoutMs: number }
  | { kind: 'click'; selector: string; text?: string; timeoutMs: number }
  | { kind: 'capture'; filename: string }
  | { kind: 'frames'; prefix: string; start: number; count: number; intervalMs: number }

export interface MediaCaptureSpec {
  schemaVersion: 1
  outputDirectory: string
  actions: MediaCaptureAction[]
}

const FORBIDDEN_VISIBLE_TEXT = [
  { label: 'absolute user path', pattern: /(?:\/Users\/|\/home\/|\/root\/|[A-Z]:\\Users\\)/i },
  { label: 'file URL', pattern: /file:\/\//i },
  { label: 'private host name', pattern: /\b[A-Za-z0-9][A-Za-z0-9.-]*\.(?:local|internal|lan)\b/i },
  { label: 'private provider name', pattern: /\b(?:JobHunter|Hermes|Tailscale)\b/i },
  { label: 'bearer credential', pattern: /\bBearer\s+[A-Za-z0-9._~-]+/i },
  { label: 'credential field', pattern: /\b(?:(?:device|mcp|access|refresh|auth|session)[_-]?token|token|api[_-]?key|client[_-]?secret|secret|private[_-]?key|password|authorization)\b\s*(?::|=)/i },
  { label: 'credential-bearing URL', pattern: /\b[A-Za-z][A-Za-z0-9+.-]*:\/\/[^\s/@:]+:[^\s/@]+@/i },
  { label: 'raw internal error', pattern: /\b(?:Error invoking remote method|Document request failed|Unhandled exception)\b/i }
]

export function mediaPrivacyViolation(text: string): string | null {
  const violation = FORBIDDEN_VISIBLE_TEXT.find(({ pattern }) => pattern.test(text))
  return violation ? `Media capture contains forbidden ${violation.label}` : null
}

const SAFE_SELECTOR = /^(?:[.#][A-Za-z0-9_-]+|[A-Za-z][A-Za-z0-9_-]*)(?:[.#[\]="': A-Za-z0-9_-]+)?$/
const SAFE_FILENAME = /^[A-Za-z0-9][A-Za-z0-9._-]*\.png$/
const SAFE_PREFIX = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`Invalid ${label}`)
  return value as Record<string, unknown>
}

function selector(value: unknown): string {
  if (typeof value !== 'string' || value.length > 240 || !SAFE_SELECTOR.test(value)) {
    throw new Error('Invalid media capture selector')
  }
  return value
}

function optionalText(value: unknown): string | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'string' || !value || value.length > 240 || /[\r\n]/.test(value)) {
    throw new Error('Invalid media capture text assertion')
  }
  return value
}

function integer(value: unknown, minimum: number, maximum: number, label: string): number {
  if (!Number.isInteger(value) || Number(value) < minimum || Number(value) > maximum) {
    throw new Error(`Invalid ${label}`)
  }
  return Number(value)
}

function exactKeys(value: Record<string, unknown>, allowed: string[]): void {
  if (Object.keys(value).some(key => !allowed.includes(key))) throw new Error('Unknown media capture field')
}

function parseAction(raw: unknown): MediaCaptureAction {
  const value = record(raw, 'media capture action')
  if (value.kind === 'wait' || value.kind === 'click') {
    exactKeys(value, ['kind', 'selector', 'text', 'timeoutMs'])
    return {
      kind: value.kind,
      selector: selector(value.selector),
      text: optionalText(value.text),
      timeoutMs: integer(value.timeoutMs, 100, 15_000, 'media capture timeout')
    }
  }
  if (value.kind === 'capture') {
    exactKeys(value, ['kind', 'filename'])
    if (typeof value.filename !== 'string' || !SAFE_FILENAME.test(value.filename)) {
      throw new Error('Invalid media capture filename')
    }
    return { kind: 'capture', filename: value.filename }
  }
  if (value.kind === 'frames') {
    exactKeys(value, ['kind', 'prefix', 'start', 'count', 'intervalMs'])
    if (typeof value.prefix !== 'string' || !SAFE_PREFIX.test(value.prefix)) {
      throw new Error('Invalid media frame prefix')
    }
    return {
      kind: 'frames',
      prefix: value.prefix,
      start: integer(value.start, 1, 9_999, 'media frame start'),
      count: integer(value.count, 1, 180, 'media frame count'),
      intervalMs: integer(value.intervalMs, 50, 1_000, 'media frame interval')
    }
  }
  throw new Error('Invalid media capture action kind')
}

export function parseMediaCaptureSpec(raw: unknown): MediaCaptureSpec {
  const value = record(raw, 'media capture spec')
  exactKeys(value, ['schemaVersion', 'outputDirectory', 'actions'])
  if (value.schemaVersion !== 1) throw new Error('Unsupported media capture spec')
  if (typeof value.outputDirectory !== 'string' || !path.isAbsolute(value.outputDirectory)) {
    throw new Error('Media capture output directory must be absolute')
  }
  if (!Array.isArray(value.actions) || value.actions.length < 1 || value.actions.length > 40) {
    throw new Error('Invalid media capture action list')
  }
  const actions = value.actions.map(parseAction)
  const outputs = actions.flatMap(action => {
    if (action.kind === 'capture') return [action.filename]
    if (action.kind !== 'frames') return []
    return Array.from({ length: action.count }, (_, index) => (
      `${action.prefix}${String(action.start + index).padStart(4, '0')}.png`
    ))
  })
  if (new Set(outputs).size !== outputs.length) throw new Error('Media capture outputs must be unique')
  const frameCount = actions.reduce((count, action) => count + (action.kind === 'frames' ? action.count : 0), 0)
  if (frameCount < 96 || frameCount > 180) throw new Error('Media capture requires 96 to 180 GIF frames')
  return { schemaVersion: 1, outputDirectory: path.resolve(value.outputDirectory), actions }
}
