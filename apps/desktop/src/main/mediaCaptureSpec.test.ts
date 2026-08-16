import { describe, expect, test } from 'vitest'

import { mediaPrivacyViolation, parseMediaCaptureSpec } from './mediaCaptureSpec.js'

const frames = { kind: 'frames', prefix: 'frame-', start: 1, count: 96, intervalMs: 83 }

describe('media capture spec', () => {
  test('accepts a contained deterministic sequence', () => {
    const parsed = parseMediaCaptureSpec({
      schemaVersion: 1,
      outputDirectory: '/tmp/jobos-media-output',
      actions: [
        { kind: 'wait', selector: '.app-shell[data-workspace="review"]', text: 'JobOS', timeoutMs: 5000 },
        { kind: 'capture', filename: 'hero.png' },
        frames
      ]
    })
    expect(parsed.actions).toHaveLength(3)
  })

  test.each([
    { ...frames, prefix: '../frame-' },
    { ...frames, count: 95 },
    { kind: 'capture', filename: '../private.png' },
    { kind: 'click', selector: 'button<script>', timeoutMs: 500 },
  ])('rejects unsafe or incomplete action %#', action => {
    expect(() => parseMediaCaptureSpec({
      schemaVersion: 1,
      outputDirectory: '/tmp/jobos-media-output',
      actions: [action]
    })).toThrow()
  })

  test('rejects duplicate outputs', () => {
    expect(() => parseMediaCaptureSpec({
      schemaVersion: 1,
      outputDirectory: '/tmp/jobos-media-output',
      actions: [frames, { ...frames, count: 1 }]
    })).toThrow('unique')
  })

  test.each([
    'Saved to /Users/example/private.docx',
    'file:///private/example.docx',
    'JobHunter adapter connected',
    'device_token: visible',
    'Authorization: Bearer ***',
    'Authorization: Basic dXNlcjpwYXNz',
    'api_key=SECRET-123',
    'password: hunter2',
    'client_secret: topsecret',
    'secret=topsecret',
    'private_key: sensitive',
    'token: sensitive',
    'session_token=sensitive',
    'postgresql://user:password@database.internal/jobs',
    'Connected to database.internal',
    'Saved to /home/alice/private.docx',
    'Connected to jacobis-macbook.local',
    "Error invoking remote method 'jobos:documents:refresh'"
  ])('rejects private visible text', text => {
    expect(mediaPrivacyViolation(text)).not.toBeNull()
  })

  test('allows the approved synthetic labels', () => {
    expect(mediaPrivacyViolation('(FAKE) Northstar Kites (Fictional Demo) · Saved')).toBeNull()
  })
})
