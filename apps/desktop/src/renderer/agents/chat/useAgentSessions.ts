import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type {
  AgentChatSelection,
  AgentConnectionState,
  AgentConversationSnapshot,
  AgentSessionJobContext,
  AgentSessionStreamUpdate,
  AgentSessionSummary,
  ConversationEvent
} from '../../../shared/contracts'
import {
  agentConversationReducer,
  initialAgentConversationState,
  projectConversation,
  type AgentConversationState
} from './useAgentConversation'

const ACTIVE_CONVERSATION_KEY = 'jobos.agent.activeConversationId'
const MAX_SESSIONS = 5

type SessionOperation = 'send' | 'stop' | 'review' | 'retry' | 'archive' | null

export interface AgentSessionViewState {
  summary: AgentSessionSummary
  conversation: AgentConversationState
  draft: string
  draftRevision: number
  restoreFailed: boolean
  operation: SessionOperation
  consumedReviewId?: string | null
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
  jobContext: { selectedJobId: null, activeArtifactId: null, activeArtifactPage: 1, activeArtifactZoom: 1 },
  binding: null,
  availability: { state: 'locked', reason: 'Agent unavailable' }
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
    draftRevision: 0,
    restoreFailed: false,
    operation: null,
    consumedReviewId: null,
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
    jobContext: snapshot.jobContext,
    binding: snapshot.binding,
    availability: snapshot.availability
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
          consumedReviewId: update.kind === 'event'
            && update.event.state === 'waiting'
            && typeof update.event.detail.approval_id === 'string'
            && update.event.detail.approval_id !== session.consumedReviewId
              ? null
              : session.consumedReviewId,
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
  const [restoring, setRestoring] = useState(available)
  const [pendingArchive, setPendingArchive] = useState<{ conversationId: string; draftRevision: number } | null>(null)
  const mountedRef = useRef(true)
  const hydrationRequests = useRef(new Map<string, Promise<void>>())
  const streamConnections = useRef(new Map<string, AgentConnectionState>())
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

  const restoreConversation = useCallback((conversationId: string): Promise<void> => {
    const bridge = window.jobos?.agent
    const session = stateRef.current.sessions[conversationId]
    if (!bridge || !session) return Promise.resolve()
    const pending = hydrationRequests.current.get(conversationId)
    if (pending) return pending
    updateState(current => {
      const currentSession = current.sessions[conversationId]
      return currentSession ? { ...current, sessions: { ...current.sessions, [conversationId]: {
        ...currentSession, conversation: { ...currentSession.conversation, restoring: true }
      } } } : current
    })
    const request = (async () => {
      try {
        const response = await bridge.get(conversationId)
        if (!mountedRef.current || response.conversationId !== conversationId) return
        updateState(current => {
          const currentSession = current.sessions[conversationId]
          if (!currentSession) return current
          // Older detail responses omit presentation metadata. Keep the sealed
          // list binding while merging the transcript with events already received.
          const snapshot = {
            ...response,
            recoveryState: response.recoveryState ?? currentSession.summary.recoveryState,
            binding: response.binding ?? currentSession.summary.binding,
            availability: response.binding ? response.availability : currentSession.summary.availability
          }
          const hasNewerEvents = currentSession.conversation.entries.some(entry => entry.eventId > snapshot.latestEventId)
          return { ...current, sessions: { ...current.sessions, [conversationId]: {
            ...currentSession,
            restoreFailed: false,
            summary: {
              ...summaryFromSnapshot(snapshot),
              recoveryState: hasNewerEvents ? currentSession.summary.recoveryState : snapshot.recoveryState
            },
            conversation: agentConversationReducer(currentSession.conversation, { type: 'hydrate', snapshot })
          } } }
        })
      } catch (error) {
        if (!mountedRef.current) return
        updateState(current => {
          const currentSession = current.sessions[conversationId]
          return currentSession ? { ...current, sessions: { ...current.sessions, [conversationId]: {
            ...currentSession,
            restoreFailed: true,
            conversation: agentConversationReducer(currentSession.conversation, {
              type: 'restore-failure', message: safeError(error, 'Conversation could not be restored')
            })
          } } } : current
        })
      } finally {
        hydrationRequests.current.delete(conversationId)
      }
    })()
    hydrationRequests.current.set(conversationId, request)
    return request
  }, [updateState])

  useEffect(() => {
    const bridge = window.jobos?.agent
    if (!bridge) return
    let mounted = true
    mountedRef.current = true
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
      const retryHydration = update.kind === 'connection' && update.state === 'online'
        && (streamConnections.current.get(update.conversationId)
          ?? current.sessions[update.conversationId]?.conversation.connection) !== 'online'
        && current.sessions[update.conversationId]?.restoreFailed
      if (update.kind === 'connection') streamConnections.current.set(update.conversationId, update.state)
      updateState(value => {
        const transition = transitionSessionUpdate(value, update)
        if (transition.announcement) setAnnouncement(transition.announcement)
        return transition.state
      })
      if (retryHydration) void restoreConversation(update.conversationId)
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
      setRestoring(false)
      if (replayAnnouncement) setAnnouncement(replayAnnouncement)
      if (activeId) window.localStorage.setItem(ACTIVE_CONVERSATION_KEY, activeId)
      await Promise.all(sorted.map(summary => restoreConversation(summary.conversationId)))
      } catch {
        if (!mounted) return
        setAnnouncement('Conversations could not be restored. Retrying…')
        retryTimer = setTimeout(() => { void restore() }, 500)
      }
    }
    void restore()
    return () => {
      mounted = false
      mountedRef.current = false
      if (retryTimer) clearTimeout(retryTimer)
      unsubscribe()
    }
  }, [restoreConversation, updateState])

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
    if (stateRef.current.sessions[conversationId]?.restoreFailed) void restoreConversation(conversationId)
    return true
  }, [restoreConversation, updateState])

  const selectByIndex = useCallback((index: number) => {
    const id = stateRef.current.order[index]
    return id ? select(id) : false
  }, [select])

  const createSession = useCallback((selection?: AgentChatSelection, initialSelectedJobId?: string | null): Promise<{
    handled: boolean
    conversationId: string | null
  }> => {
    const bridge = window.jobos?.agent
    if (!bridge || restoring) return Promise.resolve({ handled: false, conversationId: null })
    const task = createQueue.current.then(async () => {
      if (stateRef.current.order.length >= MAX_SESSIONS) {
        setAnnouncement('Maximum 5 sessions.')
        return { handled: true, conversationId: null }
      }
      setCreating(true)
      try {
        const inheritedJobId = initialSelectedJobId === undefined
          ? stateRef.current.sessions[stateRef.current.activeId ?? '']?.summary.jobContext.selectedJobId ?? null
          : initialSelectedJobId
        const snapshot = await bridge.create(selection ? { ...selection, initialSelectedJobId: inheritedJobId } : undefined)
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
        return { handled: true, conversationId: snapshot.conversationId }
      } catch {
        setAnnouncement('New session could not be started')
        return { handled: false, conversationId: null }
      } finally {
        setCreating(false)
      }
    })
    createQueue.current = task.then(() => undefined, () => undefined)
    return task
  }, [restoring, updateState])

  const create = useCallback(async (selection?: AgentChatSelection): Promise<boolean> => (
    await createSession(selection)
  ).handled, [createSession])

  const createJobless = useCallback(async (selection: AgentChatSelection): Promise<string | null> => (
    await createSession(selection, null)
  ).conversationId, [createSession])

  const setDraft = useCallback((conversationId: string, draft: string) => {
    updateState(current => {
      const session = current.sessions[conversationId]
      return session && session.operation !== 'archive'
        ? { ...current, sessions: { ...current.sessions, [conversationId]: { ...session, draft, draftRevision: session.draftRevision + 1 } } }
        : current
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
    if (!session || !text || session.summary.recoveryState !== 'ready' || session.conversation.activeTurn || !window.jobos?.agent || !beginOperation(conversationId, 'send')) return
    try {
      const mutation = await window.jobos.agent.send(conversationId, text, identifier('message-'))
      mutateConversation(conversationId, { type: 'mutation', mutation, status: 'running' })
      updateState(current => {
        const currentSession = current.sessions[conversationId]
        return currentSession && currentSession.draftRevision === session.draftRevision
          ? { ...current, sessions: { ...current.sessions, [conversationId]: { ...currentSession, draft: '' } } }
          : current
      })
    } catch (error) {
      mutateConversation(conversationId, { type: 'failure', message: safeError(error, 'Message could not be sent') })
    } finally {
      finishOperation(conversationId, 'send')
    }
  }, [beginOperation, finishOperation, mutateConversation, updateState])

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

  const review = useCallback(async (
    conversationId: string,
    turnId: string,
    approvalId: string,
    approved: boolean
  ) => {
    const turn = stateRef.current.sessions[conversationId]?.conversation.activeTurn
    if (!turn || turn.turnId !== turnId || !window.jobos?.agent || !beginOperation(conversationId, 'review')) return
    try {
      await window.jobos.agent.review(conversationId, turnId, approvalId, approved)
      updateState(current => {
        const session = current.sessions[conversationId]
        return session ? {
          ...current,
          sessions: { ...current.sessions, [conversationId]: { ...session, consumedReviewId: approvalId } }
        } : current
      })
      setAnnouncement(approved ? 'Tool approved' : 'Tool declined')
    } catch (error) {
      mutateConversation(conversationId, { type: 'failure', message: safeError(error, 'Tool review could not be submitted') })
    } finally {
      finishOperation(conversationId, 'review')
    }
  }, [beginOperation, finishOperation, mutateConversation, updateState])

  const retry = useCallback(async (conversationId: string, turnId: string) => {
    const session = stateRef.current.sessions[conversationId]
    if (!session || session.summary.recoveryState !== 'ready' || session.conversation.activeTurn || !window.jobos?.agent || !beginOperation(conversationId, 'retry')) return

    try {
      const mutation = await window.jobos.agent.retry(conversationId, turnId, identifier('retry-'))
      mutateConversation(conversationId, { type: 'mutation', mutation, status: 'running' })
    } catch (error) {
      mutateConversation(conversationId, { type: 'failure', message: safeError(error, 'Turn could not be retried') })
    } finally {
      finishOperation(conversationId, 'retry')
    }
  }, [beginOperation, finishOperation, mutateConversation])

  const cleanupRecovery = useCallback(async (conversationId: string, turnId: string) => {
    const session = stateRef.current.sessions[conversationId]
    const recovery = session && projectConversation(session.conversation.entries).find(item => (
      item.kind === 'agent-turn' && item.turnId === turnId
      && item.terminal?.detail.recovery_pending === true
      && item.terminal.detail.recovery_action === 'cleanup'
    ))
    if (!session || session.summary.recoveryState !== 'quarantined' || !recovery
      || session.conversation.activeTurn || !window.jobos?.agent || !beginOperation(conversationId, 'stop')) return
    try {
      const mutation = await window.jobos.agent.cancel(conversationId, turnId)
      mutateConversation(conversationId, { type: 'mutation', mutation, status: 'cancel-result' })
      // Recovery readiness comes from the authoritative stream or a fresh detail
      // response. A cancel acknowledgement alone does not confirm remote cleanup.
      await restoreConversation(conversationId)
    } catch (error) {
      mutateConversation(conversationId, { type: 'failure', message: safeError(error, 'Agent cleanup could not be confirmed') })
    } finally {
      finishOperation(conversationId, 'stop')
    }
  }, [beginOperation, finishOperation, mutateConversation, restoreConversation])

  const archive = useCallback(async (conversationId: string, confirmedDraftRevision?: number) => {
    const current = stateRef.current
    const session = current.sessions[conversationId]
    if (!session || creating || session.summary.recoveryState === 'recovering' || session.summary.recoveryState === 'quarantined' || session.conversation.activeTurn || session.operation || !window.jobos?.agent) return false
    if (session.draft.trim() && confirmedDraftRevision !== session.draftRevision) {
      setAnnouncement('')
      setPendingArchive({ conversationId, draftRevision: session.draftRevision })
      return false
    }
    if (!beginOperation(conversationId, 'archive')) return false
    setAnnouncement('')
    try {
      await window.jobos.agent.archive(conversationId)
      archivedIds.current.add(conversationId)
      setPendingArchive(null)
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
      setAnnouncement('Chat close was not confirmed')
      return false
    } finally {
      finishOperation(conversationId, 'archive')
    }
  }, [beginOperation, creating, finishOperation, updateState])

  const dismissArchive = useCallback(() => {
    if (pendingArchive && operations.current.get(pendingArchive.conversationId) === 'archive') return
    setPendingArchive(null)
  }, [pendingArchive])
  const confirmArchive = useCallback(async () => pendingArchive
    ? archive(pendingArchive.conversationId, pendingArchive.draftRevision)
    : false, [archive, pendingArchive])

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

  const refreshAvailability = useCallback(async () => {
    const bridge = window.jobos?.agent
    if (!bridge) return
    const summaries = await bridge.list()
    const byId = new Map(summaries.map(summary => [summary.conversationId, summary]))
    updateState(current => ({
      ...current,
      sessions: Object.fromEntries(Object.entries(current.sessions).map(([id, session]) => {
        const summary = byId.get(id)
        return [id, summary ? { ...session, summary: { ...session.summary, ...summary } } : session]
      }))
    }))
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
    restoring,
    creating,
    available,
    atMaximum: state.order.length >= MAX_SESSIONS,
    select,
    selectByIndex,
    create,
    createJobless,
    archive,
    pendingArchiveId: pendingArchive?.conversationId ?? null,
    dismissArchive,
    confirmArchive,
    restoreConversation,
    cleanupRecovery,
    setDraft,
    send,
    stop,
    review,
    retry,
    refreshAvailability,
    saveScroll,
    updateJobContext
  }
}

export type AgentSessionsController = ReturnType<typeof useAgentSessions>
