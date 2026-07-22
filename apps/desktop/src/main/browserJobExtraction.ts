import type { BrowserJobExtraction } from '../shared/contracts.js'

export function validatedBrowserJobExtraction(value: unknown): BrowserJobExtraction {
  if (!value || typeof value !== 'object') throw new Error('Invalid extracted job')
  const record = value as Record<string, unknown>
  const limits: Record<keyof BrowserJobExtraction, number> = {
    companyName: 300,
    title: 500,
    canonicalUrl: 8192,
    locationText: 1000,
    descriptionText: 100_000,
    applicationUrl: 8192
  }
  const result = {} as BrowserJobExtraction
  for (const [field, limit] of Object.entries(limits) as Array<[keyof BrowserJobExtraction, number]>) {
    const text = record[field]
    if (typeof text !== 'string' || !text.trim() || text.length > limit) throw new Error('Invalid extracted job')
    result[field] = text.trim()
  }
  for (const field of ['canonicalUrl', 'applicationUrl'] as const) {
    const url = new URL(result[field])
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
      throw new Error('Invalid extracted job URL')
    }
  }
  return result
}

export function canonicalListingUrl(currentUrl: string, extractedUrl: string): string {
  const current = new URL(currentUrl)
  const extracted = new URL(extractedUrl)
  if (!['http:', 'https:'].includes(current.protocol) || current.username || current.password
    || extracted.username || extracted.password || current.origin !== extracted.origin) {
    throw new Error('Extracted job URL does not match the active browser listing')
  }
  const normalizedCurrent = `${current.origin}${current.pathname.replace(/\/$/, '')}${current.search}`
  const normalizedExtracted = `${extracted.origin}${extracted.pathname.replace(/\/$/, '')}${extracted.search}`
  if (normalizedCurrent === normalizedExtracted) return extracted.toString()
  const extractedSlug = extracted.pathname.split('/').filter(Boolean).at(-1)?.toLowerCase()
  const currentReferences = [
    ...current.pathname.split('/').filter(Boolean),
    ...Array.from(current.searchParams.values())
  ].map(value => value.toLowerCase())
  if (!extractedSlug || !currentReferences.includes(extractedSlug)) {
    throw new Error('Extracted job URL does not match the active browser listing')
  }
  return extracted.toString()
}

export function safeApplicationUrl(value: string): string {
  const url = new URL(value)
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('Invalid extracted application URL')
  }
  return url.toString()
}
