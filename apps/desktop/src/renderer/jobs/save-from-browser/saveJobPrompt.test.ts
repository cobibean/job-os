import { describe, expect, test } from 'vitest'

import {
  agentJobSavePrompt,
  isExpectedSaveNavigation,
  parseAgentJobSaveError,
  parseAgentJobSaveResult
} from './saveJobPrompt'

describe('save-from-browser prompt protocol', () => {
  test('preserves the immutable source and exact tool protocol', () => {
    const prompt = agentJobSavePrompt('job-tab', 'https://jobs.example.com/northstar')

    expect(prompt).toContain('JobOS browser tab job-tab')
    expect(prompt).toContain('https://jobs.example.com/northstar')
    expect(prompt).toContain('mcp__jobos__browser_click')
    expect(prompt).toContain('link whose href or name matches the job slug')
    expect(prompt).toContain('complete job description')
    expect(prompt).toContain('do not summarize it or cap it at 300 characters')
    expect(prompt).toContain('responsibilities, qualifications, preferred qualifications, benefits, compensation')
    expect(prompt).toContain('page displayed in that source or replacement tab is the source of truth')
    expect(prompt).toContain('selected_job, active browser tab, or workspace context may refer to a different saved job')
    expect(prompt).toContain('MUST explicitly include text_start, text_length, and include_targets')
    expect(prompt).toContain('text_start set to the returned next_text_start')
    expect(prompt).toContain('If a duplicate segment is ever returned, retry once')
    expect(prompt).toContain('list inspection does not count toward the detail-page limit')
    expect(prompt).toContain('begin detail coverage from text_start 0')
    expect(prompt).toContain('never exceed 30 detail-page snapshots')
    expect(prompt).toContain('while text remains unread')
    expect(prompt).toContain('do not call either mutation')
    expect(prompt).toContain('ERROR_BROWSER_TOOL_UNAVAILABLE')
    expect(prompt).toContain('ERROR_SOURCE_TAB_RECOVERY_FAILED')
    expect(prompt).toContain('Return JOBOS_SAVE_ERROR:ERROR_SOURCE_TAB_RECOVERY_FAILED only when tab inspection succeeded')
    expect(prompt).toContain('its MCP transport is down')
    expect(prompt).toContain('ERROR_BROWSER_SNAPSHOT_FAILED')
    expect(prompt).toContain('ERROR_PAGE_NOT_JOB_LISTING')
    expect(prompt).toContain('ERROR_LISTING_CONTENT_NOT_EXTRACTABLE')
    expect(prompt).toContain('ERROR_LISTING_COVERAGE_INCOMPLETE')
    expect(prompt).toContain('ERROR_JOB_CREATE_FAILED')
    expect(prompt).toContain('ERROR_TAB_ASSOCIATION_FAILED')
    expect(prompt).not.toContain('ERROR_REQUIRED_TOOL_UNAVAILABLE')
    expect(prompt).toContain('Only after confirming complete relevant coverage')
    expect(prompt).not.toContain('concise role description of at most 300 characters')
    expect(prompt).not.toContain('Use at most four snapshots')
    expect(prompt).toContain('JOBOS_SAVE_RESULT:')
    expect(prompt).toContain('JOBOS_SAVE_ERROR:')
    expect(prompt.match(/Call mcp__jobos__job_create_from_browser exactly once/g)).toHaveLength(1)
    expect(prompt.match(/call mcp__jobos__browser_tab_associate exactly once/g)).toHaveLength(1)
    expect(prompt).toContain('Never call mcp__jobos__browser_navigate')
    expect(prompt).toContain('mcp__jobos__browser_tab_create exactly once')
    expect(prompt).toContain('activate=false')
    expect(prompt).toContain('user may freely switch, navigate, or close browser tabs')
    expect(prompt).toContain('Do not apply or submit forms')
  })

  test('parses only exact successful terminal payloads', () => {
    expect(parseAgentJobSaveResult('JOBOS_SAVE_RESULT:{"jobId":" job-1 ","created":true,"tabId":" tab-1 "}')).toEqual({
      jobId: 'job-1',
      created: true,
      tabId: 'tab-1'
    })
    expect(parseAgentJobSaveResult('JOBOS_SAVE_RESULT:{"jobId":"","created":true,"tabId":"tab-1"}')).toBeNull()
    expect(parseAgentJobSaveResult(' JOBOS_SAVE_RESULT:{"jobId":"job-1","created":true,"tabId":"tab-1"}')).toBeNull()
    expect(parseAgentJobSaveResult('JOBOS_SAVE_RESULT:not-json')).toBeNull()
  })

  test('preserves exact failed-stage messages', () => {
    expect(parseAgentJobSaveError('JOBOS_SAVE_ERROR:ERROR_LISTING_COVERAGE_INCOMPLETE')).toBe(
      'JobOS could not confirm the complete job listing. No job was saved.'
    )
    expect(parseAgentJobSaveError('JOBOS_SAVE_ERROR:ERROR_LISTING_CONTENT_NOT_EXTRACTABLE')).toBe(
      'JobOS recognized this as a job listing but could not read its description. Paste the listing text into this save session to continue.'
    )
    expect(parseAgentJobSaveError('JOBOS_SAVE_ERROR:ERROR_JOB_CREATE_FAILED')).toBe(
      'JobOS read the listing but could not save the job. You can retry.'
    )
    expect(parseAgentJobSaveError('JOBOS_SAVE_RESULT:ERROR_JOB_CREATE_FAILED')).toBe(
      'JobOS read the listing but could not save the job. You can retry.'
    )
    expect(parseAgentJobSaveError('JOBOS_SAVE_RESULT:ERROR_REQUIRED_TOOL_UNAVAILABLE')).toBeNull()
  })

  test('accepts only the original listing or its same-origin slug detail page', () => {
    expect(isExpectedSaveNavigation(
      'https://wellfound.com/jobs/starred?job_listing_slug=applied-ai-builder',
      'https://wellfound.com/jobs/123-applied-ai-builder'
    )).toBe(true)
    expect(isExpectedSaveNavigation(
      'https://wellfound.com/jobs/starred?job_listing_slug=applied-ai-builder',
      'https://wellfound.com/jobs/starred?job_listing_slug=unrelated-role'
    )).toBe(false)
    expect(isExpectedSaveNavigation(
      'https://wellfound.com/jobs/applied-ai-builder',
      'https://attacker.example/jobs/applied-ai-builder'
    )).toBe(false)
  })
})
