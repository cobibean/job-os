import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { afterEach, expect, test, vi } from 'vitest'

import type { AgentSessionsController, AgentSessionViewState } from '../hooks/useAgentSessions'
import { initialAgentConversationState } from '../hooks/useAgentConversation'
import { AgentSessionTabs } from './AgentSessionTabs'

afterEach(cleanup)

function session(position: number, status?: 'running' | 'waiting'): AgentSessionViewState {
  return {
    summary: {
      conversationId: `conv_${position}`, position, title: `Session ${position}`, createdAt: '', activeTurn: null,
      connection: 'online', recoveryState: 'ready', latestEventId: 0,
      jobContext: { selectedJobId: null, activeArtifactId: null, activeArtifactPage: 1, activeArtifactZoom: 1 }
    },
    conversation: {
      ...initialAgentConversationState,
      conversationId: `conv_${position}`,
      restoring: false,
      activeTurn: status ? { turnId: `turn-${position}`, status, cancelRequested: false } : null
    },
    draft: '', operation: null, unreadTerminal: false, scrollTop: 0, pinnedToBottom: true
  }
}

function controller(count = 3): AgentSessionsController {
  const order = Array.from({ length: count }, (_, index) => `conv_${index + 1}`)
  return {
    order,
    activeId: 'conv_1',
    sessions: Object.fromEntries(order.map((id, index) => [id, session(index + 1)])),
    activeSession: session(1), activeConversation: null, announcement: '', creating: false,
    available: true,
    atMaximum: count === 5,
    select: vi.fn(() => true), selectByIndex: vi.fn(() => true), create: vi.fn(async () => true),
    archive: vi.fn(async () => true), updateJobContext: vi.fn(), setDraft: vi.fn(), send: vi.fn(), stop: vi.fn(), retry: vi.fn(), saveScroll: vi.fn()
  }
}

test('arrow, Home, and End navigation selects and focuses tabs', () => {
  const value = controller()
  render(<AgentSessionTabs controller={value} />)
  const first = screen.getByRole('tab', { name: 'Session 1, Idle' })
  fireEvent.keyDown(first, { key: 'ArrowRight' })
  expect(value.select).toHaveBeenCalledWith('conv_2')
  expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Session 2, Idle' }))
  fireEvent.keyDown(document.activeElement!, { key: 'End' })
  expect(value.select).toHaveBeenCalledWith('conv_3')
})

test('five-session cap disables add with an exact label and close constraints are explicit', () => {
  const value = controller(5)
  value.activeId = 'conv_2'
  value.sessions.conv_2 = session(2, 'running')
  render(<AgentSessionTabs controller={value} />)
  expect((screen.getByRole('button', { name: 'New agent session' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByRole('button', { name: 'New agent session' }).getAttribute('title')).toBe('Maximum 5 sessions')
  const close = screen.getByRole('button', { name: 'Close Session 2' }) as HTMLButtonElement
  expect(close.disabled).toBe(true)
  expect(close.title).toBe('Stop this session before closing')
})

test('tablist contains only tabs while add and close remain toolbar controls', () => {
  const value = controller(3)
  const { container } = render(<AgentSessionTabs controller={value} />)
  const tablist = screen.getByRole('tablist', { name: 'Agent sessions' })
  expect([...tablist.children].every(child => child.getAttribute('role') === 'tab')).toBe(true)
  expect(tablist.querySelector('[aria-label^="Close"]')).toBeNull()
  expect(tablist.querySelector('[aria-label="New agent session"]')).toBeNull()
  expect(screen.getByRole('toolbar', { name: 'Agent session controls' })).not.toBeNull()
  expect(container.querySelectorAll('.agent-session-tab')).toHaveLength(3)
})

test('compact-width markup keeps every tab reachable with one roving tab stop', () => {
  const value = controller(5)
  const { container } = render(<div style={{ width: 320 }}><AgentSessionTabs controller={value} /></div>)
  expect(screen.getAllByRole('tab')).toHaveLength(5)
  expect(screen.getAllByRole('tab').filter(tab => tab.getAttribute('tabindex') === '0')).toHaveLength(1)
  expect(container.querySelector('.agent-session-tabs')).not.toBeNull()
  const tablist = screen.getByRole('tablist', { name: 'Agent sessions' })
  expect(tablist.style.getPropertyValue('--agent-session-count')).toBe('5')
  for (const tab of screen.getAllByRole('tab')) {
    expect(tab.getAttribute('title')).toMatch(/^Session \d — Idle$/)
  }
  const styles = readFileSync('src/renderer/styles.css', 'utf8')
  expect(styles).toMatch(/grid-template-columns:\s*repeat\(var\(--agent-session-count\), minmax\(36px, 1fr\)\)/)
  expect(styles).toMatch(/\.agent-session-tab\s*\{[^}]*min-width:\s*36px;[^}]*min-height:\s*40px;/s)
  expect(styles).toMatch(/@media \(max-width: 720px\), \(min-resolution: 2dppx\)/)
  expect(styles).toMatch(/@container \(max-width: 285px\)[\s\S]*\.agent-session-toolbar\s*\{\s*flex-wrap:\s*wrap;/)
  expect(styles).toMatch(/\.agent-session-tabs\s*\{\s*flex-basis:\s*100%;/)
  expect(styles).not.toMatch(/\.agent-session-tabs\s*\{[^}]*overflow-x:\s*auto/s)
})

test('recovery lifecycle is truthful and prevents close even without an active turn', () => {
  const value = controller(2)
  value.sessions.conv_1!.summary.recoveryState = 'quarantined'
  render(<AgentSessionTabs controller={value} />)
  expect(screen.getByRole('tab', { name: 'Session 1, Quarantined' })).not.toBeNull()
  const close = screen.getByRole('button', { name: 'Close Session 1' }) as HTMLButtonElement
  expect(close.disabled).toBe(true)
  expect(close.title).toBe('Recover quarantined remote work before closing')
})

test('an interrupted background terminal is never labelled idle', () => {
  const value = controller(2)
  value.sessions.conv_2!.conversation.entries = [{
    eventId: 1, turnId: 'turn-2', type: 'status', state: 'interrupted', summary: 'Interrupted',
    detail: { retry: true }, occurredAt: '2026-08-16T10:00:00Z'
  }]
  value.sessions.conv_2!.unreadTerminal = true
  render(<AgentSessionTabs controller={value} />)
  expect(screen.getByRole('tab', { name: 'Session 2, Interrupted' })).not.toBeNull()
  expect(screen.queryByRole('tab', { name: 'Session 2, Idle' })).toBeNull()
})

test.each(['send', 'retry', 'archive', 'stop'] as const)('close is disabled throughout a pending %s operation', operation => {
  const value = controller(2)
  value.sessions.conv_1!.operation = operation
  render(<AgentSessionTabs controller={value} />)
  const close = screen.getByRole('button', { name: 'Close Session 1' }) as HTMLButtonElement
  expect(close.disabled).toBe(true)
  expect(close.title).toContain(`pending ${operation}`)
})

test('close is disabled while a new session is being created', () => {
  const value = controller(2)
  value.creating = true
  render(<AgentSessionTabs controller={value} />)
  const close = screen.getByRole('button', { name: 'Close Session 1' }) as HTMLButtonElement
  expect(close.disabled).toBe(true)
  expect(close.title).toBe('Wait for the new session to finish opening')
})
