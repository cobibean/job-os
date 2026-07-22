export function safeExternalUrl(value: unknown): string | null {
  if (typeof value !== 'string' || !value || value.length > 8_192) return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}
