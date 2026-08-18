import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'

import { AgentAvatar } from './AgentAvatar'

afterEach(cleanup)

test('renders the selected avatar with stable semantic presentation metadata', () => {
  const { container } = render(<AgentAvatar avatarId="ninja" size="message" state="working" />)

  const avatar = container.querySelector('[data-agent-avatar-id="ninja"]')
  expect(avatar?.getAttribute('data-agent-avatar-state')).toBe('working')
  expect(avatar?.classList.contains('agent-avatar-message')).toBe(true)
  expect(avatar?.querySelector('img')?.getAttribute('src')).toContain('ninja.webp')
})
