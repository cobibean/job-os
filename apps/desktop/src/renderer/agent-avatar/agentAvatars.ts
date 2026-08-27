import crimsonAvatar from './assets/crimson.webp'
import emberAvatar from './assets/ember.webp'
import forestAvatar from './assets/forest.webp'
import ivoryAvatar from './assets/ivory.webp'
import midnightAvatar from './assets/midnight.webp'
import mintAvatar from './assets/mint.webp'
import ninjaAvatar from './assets/ninja.webp'
import phantomAvatar from './assets/phantom.webp'
import shadowAvatar from './assets/shadow.webp'
import starlightAvatar from './assets/starlight.webp'
import venomAvatar from './assets/venom.webp'

export const AGENT_AVATARS = [
  {
    id: 'ninja',
    label: 'Ninja',
    description: 'Focused and ready',
    src: ninjaAvatar
  },
  {
    id: 'forest',
    label: 'Ranger',
    description: 'Grounded and alert',
    src: forestAvatar
  },
  {
    id: 'crimson',
    label: 'Crimson',
    description: 'Sharp and playful',
    src: crimsonAvatar
  },
  {
    id: 'midnight',
    label: 'Sentry',
    description: 'Calm and determined',
    src: midnightAvatar
  },
  {
    id: 'mint',
    label: 'Mint',
    description: 'Bright and friendly',
    src: mintAvatar
  },
  {
    id: 'ember',
    label: 'Ember',
    description: 'Warm and watchful',
    src: emberAvatar
  },
  {
    id: 'venom',
    label: 'Venom',
    description: 'Bold and relentless',
    src: venomAvatar
  },
  {
    id: 'shadow',
    label: 'Shadow',
    description: 'Quiet and precise',
    src: shadowAvatar
  },
  {
    id: 'starlight',
    label: 'Starlight',
    description: 'Curious and optimistic',
    src: starlightAvatar
  },
  {
    id: 'phantom',
    label: 'Phantom',
    description: 'Stealthy and intense',
    src: phantomAvatar
  },
  {
    id: 'ivory',
    label: 'Ivory',
    description: 'Classic and composed',
    src: ivoryAvatar
  }
] as const

export type AgentAvatarId = typeof AGENT_AVATARS[number]['id']

export const DEFAULT_AGENT_AVATAR_ID: AgentAvatarId = 'ninja'

export function isAgentAvatarId(value: unknown): value is AgentAvatarId {
  return typeof value === 'string' && AGENT_AVATARS.some(avatar => avatar.id === value)
}

export function getAgentAvatar(avatarId: AgentAvatarId) {
  return AGENT_AVATARS.find(avatar => avatar.id === avatarId) ?? AGENT_AVATARS[0]
}
