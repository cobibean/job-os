import { describe, expect, it } from 'vitest'
import { AGENT_SYSTEM_PROMPT } from '../src/ai/protocol.js'

describe('document assistant prompt contract', () => {
  it('preserves explicit unsupported claims while prohibiting autonomous invention', () => {
    expect(AGENT_SYSTEM_PROMPT).toContain('Never silently invent, infer, or add factual claims')
    expect(AGENT_SYSTEM_PROMPT).toContain(
      'Explicit user-provided or user-approved claims may be included without supporting Evidence or independent proof',
    )
    expect(AGENT_SYSTEM_PROMPT).toContain(
      'Never silently remove, omit, weaken, or rewrite explicit user content solely because it is unsupported, uncertain, or conflicting',
    )
    expect(AGENT_SYSTEM_PROMPT).toContain('offer a non-blocking warning')
  })

  it('distinguishes user-supplied and hypothetical facts from agent fabrication', () => {
    expect(AGENT_SYSTEM_PROMPT).toContain(
      'Do not describe explicit user-supplied or clearly hypothetical content as agent fabrication',
    )
    expect(AGENT_SYSTEM_PROMPT).toContain(
      'Charts may use document data, explicit user-supplied data, search results, or clearly labeled hypothetical data',
    )
  })
})
