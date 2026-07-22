import { describe, expect, test } from 'vitest'

import { canonicalListingUrl, safeApplicationUrl, validatedBrowserJobExtraction } from './browserJobExtraction.js'

const extraction = {
  companyName: 'Northstar Labs',
  title: 'Senior Engineer',
  canonicalUrl: 'https://example.com/jobs/17',
  locationText: 'Remote',
  descriptionText: 'Build useful things.',
  applicationUrl: 'https://example.com/jobs/17/apply'
}

describe('browser job extraction URL validation', () => {
  test('accepts credential-free HTTP job URLs', () => {
    expect(validatedBrowserJobExtraction(extraction)).toEqual(extraction)
    expect(canonicalListingUrl(extraction.canonicalUrl, extraction.canonicalUrl)).toBe('https://example.com/jobs/17')
    expect(safeApplicationUrl(extraction.applicationUrl)).toBe('https://example.com/jobs/17/apply')
  })

  test.each([
    ['canonical URL', { ...extraction, canonicalUrl: 'https://user:secret@example.com/jobs/17' }],
    ['application URL', { ...extraction, applicationUrl: 'https://user:secret@example.com/jobs/17/apply' }]
  ])('rejects embedded credentials in the %s', (_label, candidate) => {
    expect(() => validatedBrowserJobExtraction(candidate)).toThrow('Invalid extracted job URL')
  })

  test('rejects embedded credentials during canonical listing reconciliation', () => {
    expect(() => canonicalListingUrl(
      extraction.canonicalUrl,
      'https://user:secret@example.com/jobs/17'
    )).toThrow('Extracted job URL does not match the active browser listing')
  })
})
