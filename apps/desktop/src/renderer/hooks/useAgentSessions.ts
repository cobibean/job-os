import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type {
  AgentConversationSnapshot,
  AgentSessionJobContext,
  AgentSessionStreamUpdate,
  AgentSessionSummary,
  ConversationEvent
} from '../../shared/contracts'
import {
  agentConversationReducer,
  initialAgentConversationState,
  projectConversation,
  type AgentConversationState
} from './useAgentConversation'

const ACTIVE_CONVERSATION_KEY = 'jobos.agent.activeConversationId'
const MAX_SESSIONS = 5

type SessionOperation = 'send' | 'stop' | 'retry' | 'archive' | null

export interface AgentSessionViewState {
  summary: AgentSessionSummary
  conversation: AgentConversationState
  draft: string
  operation: SessionOperation
  unreadTerminal: boolean
  scrollTop: number
  pinnedToBottom: boolean
}

interface AgentSessionsState {
  order: string[]
  activeId: string | null
  sessions: Record<string, AgentSessionViewState>
}

const unavailableSummary: AgentSessionSummary = {
  conversationId: 'conv_unavailable', position: 1, title: 'Session 1', createdAt: '',
  activeTurn: null, connection: 'offline', recoveryState: 'ready', latestEventId: 0,
  jobContext: { selectedJobId: null, activeArtifactId: null, activeArtifactPage: 1, activeArtifactZoom: 1 }
}

function identifier(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `desktop-${prefix}${random}`
}

function safeError(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) return error.message.trim().slice(0, 500)
  return fallback
}

function sessionFromSummary(summary: AgentSessionSummary): AgentSessionViewState {
  return {
    summary: { ...summary, recoveryState: summary.recoveryState ?? 'ready' },
    conversation: {
      ...initialAgentConversationState,
      conversationId: summary.conversationId,
      activeTurn: summary.activeTurn,
      connection: summary.connection
    },
    draft: '',
    operation: null,
    unreadTerminal: false,
    scrollTop: 0,
    pinnedToBottom: true
  }
}

function summaryFromSnapshot(snapshot: AgentConversationSnapshot): AgentSessionSummary {
  return {
    conversationId: snapshot.conversationId,
    position: snapshot.position ?? 1,
    title: snapshot.title ?? 'Session 1',
    createdAt: snapshot.createdAt ?? new Date(0).toISOString(),
    activeTurn: snapshot.activeTurn,
    connection: snapshot.connection,
    recoveryState: snapshot.recoveryState ?? 'ready',
    latestEventId: snapshot.latestEventId,
    jobContext: snapshot.jobContext
  }
}

function isAttentionEvent(event: ConversationEvent): boolean {
  return event.state === 'waiting'
    || ((event.type === 'assistant_message' || event.type === 'error' || event.type === 'status')
      && ['completed', 'failed', 'interrupted'].includes(event.state))
}

function terminalLabel(event: ConversationEvent): string | null {
  if (event.state === 'waiting') return 'needs you'
  if (event.state === 'failed') return 'failed'
  if (event.state === 'completed' && event.type === 'assistant_message') return 'completed'
  return null
}

function transitionSessionUpdate(
  state: AgentSessionsState,
  update: AgentSessionStreamUpdate
): { state: AgentSessionsState; announcement: string | null } {
  const session = state.sessions[update.conversationId]
  if (!session) return { state, announcement: null }
  const conversation = update.kind === 'event'
    ? agentConversationReducer(session.conversation, { type: 'event', event: update.event })
    : agentConversationReducer(session.conversation, { type: 'connection', state: update.state })
  const inactiveAttention = state.activeId !== update.conversationId
    && update.kind === 'event'
    && isAttentionEvent(update.event)
  const label = state.activeId !== update.conversationId && update.kind === 'event'
    ? terminalLabel(update.event)
    : null
  const position = state.order.indexOf(update.conversationId) + 1
  return {
    state: {
      ...state,
      sessions: {
        ...state.sessions,
        [update.conversationId]: {
          ...session,
          summary: update.kind === 'event'
            ? { ...session.summary, recoveryState: update.recoveryState }
            : session.summary,
          conversation,
          unreadTerminal: session.unreadTerminal || inactiveAttention
        }
      }
    },
    announcement: label && position > 0 ? `Session ${position} ${label}` : null
  }
}

export function useAgentSessions() {
  const available = Boolean(window.jobos?.agent)
  const [state, setState] = useState<AgentSessionsState>(() => available
    ? { order: [], activeId: null, sessions: {} }
    : { order: [unavailableSummary.conversationId], activeId: unavailableSummary.conversationId, sessions: {
        [unavailableSummary.conversationId]: {
          ...sessionFromSummary(unavailableSummary),
          conversation: { ...sessionFromSummary(unavailableSummary).conversation, restoring: false, error: 'Agent is available in the desktop app' }
        }
      } })
  const [announcement, setAnnouncement] = useState('')
  const [creating, setCreating] = useState(false)
  const stateRef = useRef(state)
  const earlyUpdates = useRef(new Map<string, AgentSessionStreamUpdate[]>())
  const archivedIds = useRef(new Set<string>())
  const operations = useRef(new Map<string, Exclude<SessionOperation, null>>())
  const createQueue = useRef<Promise<unknown>>(Promise.resolve())

  const updateState = useCallback((updater: (current: AgentSessionsState) => AgentSessionsState) => {
    const next = updater(stateRef.current)
    stateRef.current = next
    setState(next)
  }, [])

  useEffect(() => {
    const bridge = window.jobos?.agent
    if (!bridge) return
    let mounted = true
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    const unsubscribe = bridge.subscribe(update => {
      if (archivedIds.current.has(update.conversationId)) return
      const current = stateRef.current
      if (!current.sessions[update.conversationId]) {
        const queued = earlyUpdates.current.get(update.conversationId) ?? []
        if (queued.length < 1_000) queued.push(update)
        earlyUpdates.current.set(update.conversationId, queued)
        return
      }
      updateState(value => {
        const transition = transitionSessionUpdate(value, update)
        if (transition.announcement) setAnnouncement(transition.announcement)
        return transition.state
      })
    })

    const restore = async () => {
      try {
        const summaries = await bridge.list()
        if (!mounted) return
      const sorted = [...summaries].sort((left, right) => left.position - right.position).slice(0, MAX_SESSIONS)
      const stored = window.localStorage.getItem(ACTIVE_CONVERSATION_KEY)
      const activeId = sorted.some(item => item.conversationId === stored) ? stored : (sorted[0]?.conversationId ?? null)
      const sessions: Record<string, AgentSessionViewState> = {}
      for (const summary of sorted) {
        sessions[summary.conversationId] = sessionFromSummary(summary)
      }
      let restoredState: AgentSessionsState = { order: sorted.map(item => item.conversationId), activeId, sessions }
      let replayAnnouncement: string | null = null
      for (const summary of sorted) {
        for (const update of earlyUpdates.current.get(summary.conversationId) ?? []) {
          const transition = transitionSessionUpdate(restoredState, update)
          restoredState = transition.state
          replayAnnouncement = transition.announcement ?? replayAnnouncement
        }
        earlyUpdates.current.delete(summary.conversationId)
      }
      updateState(() => restoredState)
      if (replayAnnouncement) setAnnouncement(replayAnnouncement)
      if (activeId) window.localStorage.setItem(ACTIVE_CONVERSATION_KEY, activeId)
      await Promise.all(sorted.map(async summary => {
        try {
          const snapshot = await bridge.get(summary.conversationId)
          if (!mounted || snapshot.conversationId !== summary.conversationId) return
          updateState(current => {
            const session = current.sessions[summary.conversationId]
            if (!session) return current
            return {
              ...current,
              sessions: {
                ...current.sessions,
                [summary.conversationId]: {
                  ...session,
                  summary: summaryFromSnapshot(snapshot),
                  conversation: agentConversationReducer(session.conversation, { type: 'hydrate', snapshot })
                }
              }
            }
          })
        } catch (error) {
          updateState(current => {
            const session = current.sessions[summary.conversationId]
            if (!session) return current
            return {
              ...current,
              sessions: {
                ...current.sessions,
                [summary.conversationId]: {
                  ...session,
                  conversation: agentConversationReducer(session.conversation, {
                    type: 'restore-failure', message: safeError(error, 'Conversation could not be restored')
                  })
                }
              }
            }
          })
        }
      }))
      } catch {
        if (!mounted) return
        setAnnouncement('Conversations could not be restored. Retrying…')
        retryTimer = setTimeout(() => { void restore() }, 500)
      }
    }
    void restore()
    return () => {
      mounted = false
      if (retryTimer) clearTimeout(retryTimer)
      unsubscribe()
    }
  }, [updateState])

  const select = useCallback((conversationId: string) => {
    if (!stateRef.current.sessions[conversationId]) return false
    updateState(current => ({
      ...current,
      activeId: conversationId,
      sessions: {
        ...current.sessions,
        [conversationId]: { ...current.sessions[conversationId]!, unreadTerminal: false }
      }
    }))
    window.localStorage.setItem(ACTIVE_CONVERSATION_KEY, conversationId)
    return true
  }, [updateState])

  const selectByIndex = useCallback((index: number) => {
    const id = stateRef.current.order[index]
    return id ? select(id) : false
  }, [select])

  const create = useCallback((initialSelectedJobId?: string | null): Promise<boolean> => {
    const bridge = window.jobos?.agent
    if (!bridge) return Promise.resolve(false)
    const task = createQueue.current.then(async (): Promise<boolean> => {
      if (stateRef.current.order.length >= MAX_SESSIONS) {
        setAnnouncement('Maximum 5 sessions.')
        return true
      }
      setCreating(true)
      try {
        const inheritedJobId = initialSelectedJobId === undefined
          ? stateRef.current.sessions[stateRef.current.activeId ?? '']?.summary.jobContext.selectedJobId ?? null
          : initialSelectedJobId
        const snapshot = await bridge.create(inheritedJobId)
        let session: AgentSessionViewState = {
          ...sessionFromSummary(summaryFromSnapshot(snapshot)),
          conversation: agentConversationReducer(initialAgentConversationState, { type: 'hydrate', snapshot })
        }
        let createdState: AgentSessionsState = {
          order: [...stateRef.current.order, snapshot.conversationId],
          activeId: snapshot.conversationId,
          sessions: { ...stateRef.current.sessions, [snapshot.conversationId]: session }
        }
        for (const update of earlyUpdates.current.get(snapshot.conversationId) ?? []) {
          createdState = transitionSessionUpdate(createdState, update).state
        }
        earlyUpdates.current.delete(snapshot.conversationId)
        updateState(() => createdState)
        window.localStorage.setItem(ACTIVE_CONVERSATION_KEY, snapshot.conversationId)
        setAnnouncement(`Session ${stateRef.current.order.length} created`)
        return true
      } catch {
        setAnnouncement('New session could not be started')
        return false
      } finally {
        setCreating(false)
      }
    })
    createQueue.current = task.then(() => undefined, () => undefined)
    return task
  }, [updateState])

  const setDraft = useCallback((conversationId: string, draft: string) => {
    updateState(current => {
      const session = current.sessions[conversationId]
      return session ? { ...current, sessions: { ...current.sessions, [conversationId]: { ...session, draft } } } : current
    })
  }, [updateState])

  const beginOperation = useCallback((conversationId: string, operation: Exclude<SessionOperation, null>) => {
    if (operations.current.has(conversationId)) return false
    operations.current.set(conversationId, operation)
    updateState(current => {
      const session = current.sessions[conversationId]
      return session ? { ...current, sessions: { ...current.sessions, [conversationId]: { ...session, operation } } } : current
    })
    return true
  }, [updateState])

  const finishOperation = useCallback((conversationId: string, operation: Exclude<SessionOperation, null>) => {
    if (operations.current.get(conversationId) !== operation) return
    operations.current.delete(conversationId)
    updateState(current => {
      const session = current.sessions[conversationId]
      return session ? { ...current, sessions: { ...current.sessions, [conversationId]: { ...session, operation: null } } } : current
    })
  }, [updateState])

  const mutateConversation = useCallback((conversationId: string, action: Parameters<typeof agentConversationReducer>[1]) => {
    updateState(current => {
      const session = current.sessions[conversationId]
      return session ? {
        ...current,
        sessions: { ...current.sessions, [conversationId]: { ...session, conversation: agentConversationReducer(session.conversation, action) } }
      } : current
    })
  }, [updateState])

  const send = useCallback(async (conversationId: string) => {
    const session = stateRef.current.sessions[conversationId]
    const text = session?.draft.trim()
    if (!session || !text || session.conversation.activeTurn || !window.jobos?.agent || !beginOperation(conversationId, 'send')) return
    try {
      const mutation = await window.jobos.agent.send(conversationId, text, identifier('message-'))
      updateState(current => {
        const currentSession = current.sessions[conversationId]
        return currentSession ? { ...current, sessions: { ...current.sessions, [conversationId]: {
          ...currentSession, summary: { ...currentSession.summary, recoveryState: 'ready' }
        } } } : current
      })
      mutateConversation(conversationId, { type: 'mutation', mutation, status: 'running' })
      setDraft(conversationId, '')
    } catch (error) {
      mutateConversation(conversationId, { type: 'failure', message: safeError(error, 'Message could not be sent') })
    } finally {
      finishOperation(conversationId, 'send')
    }
  }, [beginOperation, finishOperation, mutateConversation, setDraft])

  const stop = useCallback(async (conversationId: string) => {
    const turn = stateRef.current.sessions[conversationId]?.conversation.activeTurn
    if (!turn || !window.jobos?.agent || !beginOperation(conversationId, 'stop')) return
    try {
      const mutation = await window.jobos.agent.cancel(conversationId, turn.turnId)
      mutateConversation(conversationId, { type: 'mutation', mutation, status: 'cancel-result' })
    } catch (error) {
      mutateConversation(conversationId, { type: 'failure', message: safeError(error, 'Turn could not be stopped') })
    } finally {
      finishOperation(conversationId, 'stop')
    }
  }, [beginOperation, finishOperation, mutateConversation])

  const retry = useCallback(async (conversationId: string, turnId: string) => {
    const session = stateRef.current.sessions[conversationId]
    if (!session || session.conversation.activeTurn || !window.jobos?.agent || !beginOperation(conversationId, 'retry')) return
    try {
      const mutation = await window.jobos.agent.retry(conversationId, turnId, identifier('retry-'))
      updateState(current => {
        const currentSession = current.sessions[conversationId]
        return currentSession ? { ...current, sessions: { ...current.sessions, [conversationId]: {
          ...currentSession, summary: { ...currentSession.summary, recoveryState: 'ready' }
        } } } : current
      })
      mutateConversation(conversationId, { type: 'mutation', mutation, status: 'running' })
    } catch (error) {
      mutateConversation(conversationId, { type: 'failure', message: safeError(error, 'Turn could not be retried') })
    } finally {
      finishOperation(conversationId, 'retry')
    }
  }, [beginOperation, finishOperation, mutateConversation])

  const archive = useCallback(async (conversationId: string) => {
    const current = stateRef.current
    const session = current.sessions[conversationId]
    if (!session || current.order.length <= 1 || creating || session.summary.recoveryState === 'recovering' || session.summary.recoveryState === 'quarantined' || session.conversation.activeTurn || session.operation || !window.jobos?.agent) return false
    if (!beginOperation(conversationId, 'archive')) return false
    try {
      await window.jobos.agent.archive(conversationId)
      archivedIds.current.add(conversationId)
      updateState(value => {
        const index = value.order.indexOf(conversationId)
        const order = value.order.filter(id => id !== conversationId)
        const sessions = { ...value.sessions }
        delete sessions[conversationId]
        const activeId = value.activeId === conversationId ? (order[Math.min(index, order.length - 1)] ?? null) : value.activeId
        if (activeId) window.localStorage.setItem(ACTIVE_CONVERSATION_KEY, activeId)
        else window.localStorage.removeItem(ACTIVE_CONVERSATION_KEY)
        return { order, sessions, activeId }
      })
      return true
    } catch {
      setAnnouncement('Session could not be closed')
      return false
    } finally {
      finishOperation(conversationId, 'archive')
    }
  }, [beginOperation, creating, finishOperation, updateState])

  const saveScroll = useCallback((conversationId: string, scrollTop: number, pinnedToBottom: boolean) => {
    updateState(current => {
      const session = current.sessions[conversationId]
      return session ? {
        ...current,
        sessions: { ...current.sessions, [conversationId]: { ...session, scrollTop, pinnedToBottom } }
      } : current
    })
  }, [updateState])

  const updateJobContext = useCallback((conversationId: string, jobContext: AgentSessionJobContext) => {
    updateState(current => {
      const session = current.sessions[conversationId]
      return session ? {
        ...current,
        sessions: { ...current.sessions, [conversationId]: {
          ...session, summary: { ...session.summary, jobContext }
        } }
      } : current
    })
  }, [updateState])

  const activeSession = state.activeId ? state.sessions[state.activeId] ?? null : null
  const activeConversation = useMemo(() => activeSession ? {
    ...activeSession.conversation,
    items: projectConversation(activeSession.conversation.entries),
    draft: activeSession.draft,
    operationPending: activeSession.operation !== null
  } : null, [activeSession])

  return {
    ...state,
    activeSession,
    activeConversation,
    announcement,
    creating,
    available,
    atMaximum: state.order.length >= MAX_SESSIONS,
    select,
    selectByIndex,
    create,
    archive,
    setDraft,
    send,
    stop,
    retry,
    saveScroll,
    updateJobContext
  }
}

export type AgentSessionsController = ReturnType<typeof useAgentSessions>
