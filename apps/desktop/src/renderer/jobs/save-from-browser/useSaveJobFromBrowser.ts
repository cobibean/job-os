import { useCallback, useEffect, useRef, useState } from 'react'

import type {
  BrowserState,
  BrowserTab,
  JobOsRendererBridge
} from '../../../shared/contracts'
import {
  agentJobSavePrompt,
  isExpectedSaveNavigation,
  parseAgentJobSaveError,
  parseAgentJobSaveResult
} from './saveJobPrompt'

export type SaveFeedback = {
  status: 'idle' | 'saving' | 'saved' | 'existing' | 'error'
  message: string
}

interface SaveOperation {
  operationId: string
  sourceTabId: string
  sourceUrl: string
  conversationId: string | null
  turnId: string | null
  reconciling: boolean
  reconcilePending: boolean
}

type SaveAgent = Pick<JobOsRendererBridge['agent'], 'get' | 'send' | 'subscribe'>
type SaveJobs = Pick<JobOsRendererBridge['jobs'], 'getState'>

interface SaveBrowser {
  getState: () => Promise<BrowserState>
  reconcileExternalState: (state: BrowserState) => void | Promise<void>
}

interface UseSaveJobFromBrowserOptions {
  active?: boolean
  agent: SaveAgent | null | undefined
  browser: SaveBrowser | null | undefined
  jobs: SaveJobs | null | undefined
  onCreateSaveSession: () => Promise<string | null>
  onJobSaved: (jobId: string, conversationId: string) => Promise<void>
}

function browserSaveKey(): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `browser-save-${random}`
}

export function useSaveJobFromBrowser(options: UseSaveJobFromBrowserOptions) {
  const active = options.active ?? true
  const activeRef = useRef(active)
  activeRef.current = active
  const agent = useRef(options.agent).current
  const browser = useRef(options.browser).current
  const jobs = useRef(options.jobs).current
  const latestCallbacks = useRef({
    onCreateSaveSession: options.onCreateSaveSession,
    onJobSaved: options.onJobSaved
  })
  latestCallbacks.current = {
    onCreateSaveSession: options.onCreateSaveSession,
    onJobSaved: options.onJobSaved
  }
  const [saveStates, setSaveStates] = useState<Record<string, SaveFeedback>>({})
  const saveOperations = useRef(new Map<string, SaveOperation>())

  const setTabSaveState = useCallback((tabId: string, feedback: SaveFeedback) => {
    setSaveStates(current => ({ ...current, [tabId]: feedback }))
  }, [])

  const reconcileAgentTurn = useCallback(async (operationId: string) => {
    const operation = saveOperations.current.get(operationId)
    if (!activeRef.current || !operation?.turnId || !operation.conversationId || !agent || !browser || !jobs) return
    if (operation.reconciling) {
      operation.reconcilePending = true
      return
    }
    operation.reconciling = true
    try {
      do {
        operation.reconcilePending = false
        let snapshots: Awaited<ReturnType<typeof Promise.all<[
          ReturnType<SaveAgent['get']>,
          ReturnType<SaveBrowser['getState']>
        ]>>>
        try {
          snapshots = await Promise.all([
            agent.get(operation.conversationId),
            browser.getState()
          ])
        } catch {
          if (saveOperations.current.has(operationId)) {
            setTabSaveState(operation.sourceTabId, { status: 'error', message: 'Could not confirm the extraction result. You can retry.' })
            saveOperations.current.delete(operationId)
          }
          return
        }
        if (!activeRef.current || !saveOperations.current.has(operationId)) return
        const [conversation, browserState] = snapshots
        const terminal = [...conversation.entries].reverse().find(entry => (
          entry.turnId === operation.turnId
          && ((entry.type === 'error' && entry.state === 'failed')
            || (entry.type === 'assistant_message' && entry.state === 'completed')
            || (['turn', 'status'].includes(entry.type)
              && ['failed', 'interrupted'].includes(entry.state)))
        ))
        if (!terminal) continue
        const responseText = terminal.type === 'assistant_message' && typeof terminal.detail.text === 'string'
          ? terminal.detail.text
          : terminal.summary
        const saveResult = terminal.type === 'assistant_message'
          ? parseAgentJobSaveResult(responseText)
          : null
        const saveError = terminal.type === 'assistant_message'
          ? parseAgentJobSaveError(responseText)
          : null
        const resultTab = saveResult
          ? browserState.tabs.find(tab => tab.tabId === saveResult.tabId)
          : null
        const resultUrlAccepted = Boolean(resultTab && (
          resultTab.url === operation.sourceUrl
          || isExpectedSaveNavigation(operation.sourceUrl, resultTab.url)
        ))
        if (saveResult && resultTab && resultUrlAccepted) {
          try {
            if (resultTab.associatedJobId !== saveResult.jobId) {
              throw new Error('Could not confirm the saved job stayed associated with this listing. You can retry.')
            }
            if (!activeRef.current) return
            await latestCallbacks.current.onJobSaved(saveResult.jobId, operation.conversationId)
            if (!activeRef.current) return
            const reconciledBrowser = await browser.getState()
            await browser.reconcileExternalState(reconciledBrowser)
            const reconciledTab = reconciledBrowser.tabs.find(tab => tab.tabId === saveResult.tabId)
            if (!activeRef.current || !saveOperations.current.has(operationId)) return
            if (reconciledTab?.associatedJobId !== saveResult.jobId
              || !isExpectedSaveNavigation(operation.sourceUrl, reconciledTab.url)) {
              throw new Error('Could not confirm the saved job stayed associated with this listing. You can retry.')
            }
            const jobsState = await jobs.getState()
            const job = jobsState.jobs.find(item => item.jobId === saveResult.jobId)
            saveOperations.current.delete(operationId)
            const feedback: SaveFeedback = {
              status: saveResult.created ? 'saved' : 'existing',
              message: job
                ? `${saveResult.created ? 'Saved to JobOS' : 'Already in JobOS'}: ${job.company} · ${job.title}`
                : saveResult.created ? 'Saved to JobOS' : 'Already in JobOS'
            }
            if (saveResult.tabId !== operation.sourceTabId) {
              setSaveStates(current => {
                const next = { ...current }
                delete next[operation.sourceTabId]
                next[saveResult.tabId] = feedback
                return next
              })
            } else {
              setTabSaveState(saveResult.tabId, feedback)
            }
            return
          } catch (error) {
            if (saveOperations.current.has(operationId)) {
              setTabSaveState(operation.sourceTabId, {
                status: 'error',
                message: error instanceof Error ? error.message : 'Could not save this job. You can retry.'
              })
              saveOperations.current.delete(operationId)
            }
            return
          }
        }
        setTabSaveState(operation.sourceTabId, {
          status: 'error',
          message: saveError ?? (terminal.type === 'error'
            ? terminal.summary || 'Job hunter could not inspect this listing'
            : 'Job hunter finished without returning usable job details. You can retry.')
        })
        saveOperations.current.delete(operationId)
        return
      } while (operation.reconcilePending && saveOperations.current.has(operationId))
    } finally {
      operation.reconciling = false
    }
  }, [agent, browser, jobs, setTabSaveState])

  useEffect(() => {
    if (!active || !agent) {
      saveOperations.current.clear()
      return undefined
    }
    return agent.subscribe(update => {
      if (update.kind !== 'event') return
      const turnId = update.event.turnId
      if (!turnId) return
      const operation = [...saveOperations.current.values()].find(item => (
        item.conversationId === update.conversationId && item.turnId === turnId
      ))
      if (!operation) return
      const isTerminal = (update.event.type === 'error' && update.event.state === 'failed')
        || (update.event.type === 'assistant_message' && update.event.state === 'completed')
        || (['turn', 'status'].includes(update.event.type)
          && ['failed', 'interrupted'].includes(update.event.state))
      if (isTerminal) void reconcileAgentTurn(operation.operationId)
    })
  }, [active, agent, reconcileAgentTurn])

  const saveJob = useCallback(async (tab: BrowserTab) => {
    if (!activeRef.current || tab.associatedJobId || saveStates[tab.tabId]?.status === 'saving') return
    const operationId = browserSaveKey()
    const operation: SaveOperation = {
      operationId,
      sourceTabId: tab.tabId,
      sourceUrl: tab.url,
      conversationId: null,
      turnId: null,
      reconciling: false,
      reconcilePending: false
    }
    saveOperations.current.set(operationId, operation)
    setTabSaveState(tab.tabId, { status: 'saving', message: 'Job hunter is reading this listing…' })
    try {
      if (!agent || !browser || !jobs) throw new Error('Could not read this job listing')
      const conversationId = await latestCallbacks.current.onCreateSaveSession()
      if (!activeRef.current || !saveOperations.current.has(operationId)) return
      if (!conversationId) throw new Error('Could not start a clean agent session. Close an existing session if five are open, then retry.')
      operation.conversationId = conversationId
      const turn = await agent.send(
        conversationId,
        agentJobSavePrompt(operation.sourceTabId, operation.sourceUrl),
        operationId
      )
      if (!activeRef.current || !saveOperations.current.has(operationId)) return
      operation.turnId = turn.turnId
      await reconcileAgentTurn(operationId)
    } catch (error) {
      saveOperations.current.delete(operationId)
      if (activeRef.current) {
        setTabSaveState(operation.sourceTabId, {
          status: 'error',
          message: error instanceof Error ? error.message : 'Could not read this job listing'
        })
      }
    }
  }, [agent, browser, jobs, reconcileAgentTurn, saveStates, setTabSaveState])

  return { saveJob, saveStates }
}
