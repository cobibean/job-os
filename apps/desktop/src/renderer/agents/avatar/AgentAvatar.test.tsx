import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'

import { AgentAvatar } from './AgentAvatar'
import { AGENT_AVATARS } from './agentAvatars'

afterEach(cleanup)

test('renders the selected avatar with stable semantic presentation metadata', () => {
  const { container } = render(<AgentAvatar avatarId="ninja" size="message" state="working" />)

  const avatar = container.querySelector('[data-agent-avatar-id="ninja"]')
  expect(avatar?.getAttribute('data-agent-avatar-state')).toBe('working')
  expect(avatar?.classList.contains('agent-avatar-message')).toBe(true)
  expect(avatar?.querySelector('img')?.getAttribute('src')).toContain('ninja.webp')
})

test('renders every bundled avatar from the selector registry', () => {
  expect(AGENT_AVATARS).toHaveLength(11)

  for (const avatarDefinition of AGENT_AVATARS) {
    const { container, unmount } = render(
      <AgentAvatar avatarId={avatarDefinition.id} size="settings" />
    )

    const avatar = container.querySelector(`[data-agent-avatar-id="${avatarDefinition.id}"]`)
    expect(avatar?.querySelector('img')?.getAttribute('src')).toContain(`${avatarDefinition.id}.webp`)
    unmount()
  }
})
