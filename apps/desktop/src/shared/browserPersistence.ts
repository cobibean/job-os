import type { BrowserTabMetadata } from './contracts.js'

const sensitiveParameter = /(?:access|auth|bearer|credential|id|refresh|session)?[_-]?(?:key|password|secret|token)|^(?:code|jwt|samlresponse)$/iu

export function sanitizeBrowserUrlForPersistence(value: string): string {
  if (value === 'about:blank') return value
  const url = new URL(value)
  url.username = ''
  url.password = ''
  url.hash = ''
  for (const key of Array.from(url.searchParams.keys())) {
    if (sensitiveParameter.test(key)) url.searchParams.delete(key)
  }
  return url.toString()
}

export function sanitizeBrowserMetadata(tab: BrowserTabMetadata): BrowserTabMetadata {
  return {
    ...tab,
    url: sanitizeBrowserUrlForPersistence(tab.url),
    faviconUrl: tab.faviconUrl?.startsWith('http://') || tab.faviconUrl?.startsWith('https://')
      ? sanitizeBrowserUrlForPersistence(tab.faviconUrl)
      : null
  }
}
