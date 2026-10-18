import { useCallback, useEffect, useRef, useState } from 'react'

import type { AgentSessionJobContext, JobDetail, JobListItem, JobSortMode, JobStatus } from '../../shared/contracts'

const JOB_STATUSES = new Set<string>([
  'discovered', 'scored', 'reviewed', 'shortlisted', 'apply_now', 'maybe',
  'stretch', 'skipped', 'applied', 'interviewing', 'closed', 'archived'
])

function statusChangeError(error: unknown): string {
  if (!(error instanceof Error)) return 'Status change failed'
  const transition = error.message.match(/Invalid lead state transition: ([a-z_]+) -> ([a-z_]+)/)
  const source = transition?.[1]
  const target = transition?.[2]
  if (source && target && JOB_STATUSES.has(source) && JOB_STATUSES.has(target)) return transition[0]
  return 'Status change failed'
}

export function useJobs(
  activeConversationId?: string | null,
  activeJobContext?: AgentSessionJobContext | null,
  onJobContextChange?: (conversationId: string, context: AgentSessionJobContext) => void,
  onLocalJobContextChange?: (context: AgentSessionJobContext) => void,
  connectivityState?: string
) {
  const [allJobs, setAllJobs] = useState<JobListItem[]>([])
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [selectedJob, setSelectedJob] = useState<JobListItem | null>(null)
  const [selectedJobDetail, setSelectedJobDetail] = useState<JobDetail | null>(null)
  const [sortMode, setSortMode] = useState<JobSortMode>('manual')
  const [query, setQuery] = useState('')
  const [statusGroup, setStatusGroup] = useState('')
  const [loading, setLoading] = useState(true)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)

  const bridge = useRef(window.jobos?.jobs).current
  const controls = useRef({ query, sortMode, statusGroup })
  controls.current = { query, sortMode, statusGroup }
  const controlsKey = JSON.stringify([query, sortMode, statusGroup])
  const currentControlsKey = useRef(controlsKey)
  currentControlsKey.current = controlsKey
  const listRequest = useRef(0)
  const canonicalJobs = useRef(allJobs)
  canonicalJobs.current = allJobs
  const initializationRequest = useRef(0)
  const currentScope = useRef({ activeConversationId, activeJobContext })
  currentScope.current = { activeConversationId, activeJobContext }
  const localSelectedJobId = useRef<string | null>(null)
  const readyRef = useRef(ready)
  readyRef.current = ready
  const detailRequest = useRef(0)
  const selectionRevision = useRef(0)
  const sortRequest = useRef(0)
  const eventRefreshTimer = useRef<number | null>(null)

  const loadDetail = useCallback(async (jobId: string | null) => {
    const request = detailRequest.current + 1
    detailRequest.current = request
    if (!jobId || !bridge || typeof bridge.inspect !== 'function') {
      setSelectedJobDetail(null)
      return
    }
    setSelectedJobDetail(null)
    try {
      const detail = await bridge.inspect(jobId)
      if (request === detailRequest.current) setSelectedJobDetail(detail)
    } catch {
      if (request === detailRequest.current) setSelectedJobDetail(null)
    }
  }, [bridge])

  const refresh = useCallback(async () => {
    if (!bridge) return
    const request = ++listRequest.current
    const key = currentControlsKey.current
    const current = controls.current
    try {
      const canonicalRequest = bridge.list('manual')
      const filteredRequest = current.sortMode === 'manual' && !current.query.trim() && !current.statusGroup
        ? canonicalRequest
        : bridge.list(current.sortMode, current.query.trim() || undefined, current.statusGroup || undefined)
      const [canonical, refreshed] = await Promise.all([canonicalRequest, filteredRequest])
      if (request !== listRequest.current || key !== currentControlsKey.current) return
      canonicalJobs.current = canonical
      setAllJobs(canonical)
      setJobs(refreshed)
      setError(null)
    } catch {
      if (request === listRequest.current && key === currentControlsKey.current) setError('Jobs unavailable')
    }
  }, [bridge])

  useEffect(() => {
    // A conversation context remains authoritative only for that conversation.
    // Without one, the renderer may project its separate local workspace context.
    const selected = activeConversationId || activeJobContext
      ? activeJobContext?.selectedJobId ?? null
      : localSelectedJobId.current
    setSelectedJobId(selected)
    setSelectedJob(current => selected
      ? allJobs.find(job => job.jobId === selected) ?? (current?.jobId === selected ? current : null)
      : null)
  }, [activeConversationId, activeJobContext?.selectedJobId, allJobs])

  useEffect(() => { void loadDetail(selectedJobId) }, [loadDetail, selectedJobId])

  useEffect(() => {
    if (!bridge) return
    const unsubscribe = bridge.subscribe(event => {
      if (event.eventType === 'job_description_updated' && event.jobId === selectedJobId) {
        void loadDetail(event.jobId)
      }
      setFeedback(event.origin === 'mcp' ? 'Agent changes synced' : 'Job changes synced')
      if (eventRefreshTimer.current !== null) window.clearTimeout(eventRefreshTimer.current)
      eventRefreshTimer.current = window.setTimeout(() => {
        eventRefreshTimer.current = null
        void refresh()
      }, 120)
    })
    return () => {
      if (eventRefreshTimer.current !== null) window.clearTimeout(eventRefreshTimer.current)
      eventRefreshTimer.current = null
      unsubscribe()
    }
  }, [bridge, loadDetail, refresh, selectedJobId])

  const initialize = useCallback(async () => {
    if (!bridge) {
      setLoading(false)
      return
    }
    const request = ++initializationRequest.current
    const initialSelectionRevision = selectionRevision.current
    const initialSortRequest = sortRequest.current
    const initialListRevision = ++listRequest.current
    setLoading(true)
    try {
      const snapshot = await bridge.getState()
      if (request !== initializationRequest.current) return
      const current = controls.current
      if (initialListRevision === listRequest.current) {
        canonicalJobs.current = snapshot.jobs
        setAllJobs(snapshot.jobs)
        if (!current.query.trim() && !current.statusGroup) setJobs(snapshot.jobs)
      }
      if (!readyRef.current && initialSortRequest === sortRequest.current) setSortMode(snapshot.sortMode)
      if (selectionRevision.current === initialSelectionRevision
        && currentScope.current.activeJobContext === undefined) {
        localSelectedJobId.current = snapshot.selectedJobId
        setSelectedJobId(snapshot.selectedJobId)
        setSelectedJob(snapshot.jobs.find(job => job.jobId === snapshot.selectedJobId) ?? null)
      }
      setReady(true)
      setError(null)
    } catch {
      if (request === initializationRequest.current) setError('Jobs unavailable')
    } finally {
      if (request === initializationRequest.current) setLoading(false)
    }
  }, [bridge])

  useEffect(() => {
    void initialize()
    return () => { initializationRequest.current += 1; listRequest.current += 1; detailRequest.current += 1; selectionRevision.current += 1; sortRequest.current += 1 }
  }, [initialize])

  useEffect(() => {
    if (!bridge || !ready) return
    const timeout = window.setTimeout(() => { void refresh() }, 120)
    return () => window.clearTimeout(timeout)
  }, [bridge, ready, controlsKey, refresh])

  const retry = useCallback(async () => {
    await initialize()
    await refresh()
  }, [initialize, refresh])

  const previousConnectivity = useRef(connectivityState)
  useEffect(() => {
    const recovered = connectivityState === 'connected' && previousConnectivity.current !== 'connected'
    previousConnectivity.current = connectivityState
    if (recovered) void retry()
  }, [connectivityState, retry])

  const selectJobForConversation = useCallback(async (conversationId: string, jobId: string) => {
    if (!bridge || !conversationId) {
      setError('No active session')
      return false
    }
    const requestRevision = selectionRevision.current + 1
    selectionRevision.current = requestRevision
    try {
      const context = await bridge.select(conversationId, jobId)
      onJobContextChange?.(conversationId, context)
      await refresh()
      if (selectionRevision.current !== requestRevision || currentScope.current.activeConversationId !== conversationId) return true
      setSelectedJobId(jobId)
      setSelectedJob(canonicalJobs.current.find(job => job.jobId === jobId) ?? null)
      setFeedback('Active job selected')
      setError(null)
      await loadDetail(jobId)
      return true
    } catch {
      if (selectionRevision.current === requestRevision && currentScope.current.activeConversationId === conversationId) setError('Selection failed')
      return false
    }
  }, [bridge, loadDetail, onJobContextChange, refresh])

  const selectJob = useCallback(async (jobId: string) => {
    if (activeConversationId) return selectJobForConversation(activeConversationId, jobId)
    if (!bridge) return false
    const request = ++selectionRevision.current
    try {
      const job = allJobs.find(candidate => candidate.jobId === jobId) ?? await bridge.inspect(jobId)
      if (request !== selectionRevision.current || currentScope.current.activeConversationId) return false
      localSelectedJobId.current = jobId
      const localContext = currentScope.current.activeJobContext
      onLocalJobContextChange?.(localContext?.selectedJobId === jobId
        ? localContext
        : { selectedJobId: jobId, activeArtifactId: null, activeArtifactPage: 1, activeArtifactZoom: 1 })
      setSelectedJobId(jobId)
      setSelectedJob(job)
      setFeedback('Active job selected')
      setError(null)
      await loadDetail(jobId)
      return true
    } catch {
      if (request === selectionRevision.current && !currentScope.current.activeConversationId) setError('Selection failed')
      return false
    }
  }, [activeConversationId, allJobs, bridge, loadDetail, onLocalJobContextChange, selectJobForConversation])

  const changeStatus = useCallback(async (jobId: string, status: JobStatus) => {
    if (!bridge) return
    try {
      const result = await bridge.updateStatus(jobId, status)
      listRequest.current += 1
      setJobs(current => current.map(job => job.jobId === jobId ? result.job : job).filter(job => !statusGroup || job.statusGroup === statusGroup))
      setAllJobs(current => current.map(job => job.jobId === jobId ? result.job : job))
      setSelectedJob(current => current?.jobId === jobId ? result.job : current)
      if (selectedJobId === jobId) void loadDetail(jobId)
      setFeedback(`Status changed to ${status}`)
      setError(null)
    } catch (statusError) {
      setError(statusChangeError(statusError))
    }
  }, [bridge, loadDetail, selectedJobId, statusGroup])

  const changeSort = useCallback(async (sort: JobSortMode) => {
    if (!bridge) return
    const request = ++sortRequest.current
    try {
      await bridge.setSort(sort)
      if (request !== sortRequest.current) return
      setSortMode(sort)
      setFeedback(`Ordered by ${sort}`)
      setError(null)
    } catch {
      if (request === sortRequest.current) setError('Ordering failed')
    }
  }, [bridge])

  const removeDemo = useCallback(async (jobId: string) => {
    if (!bridge) return
    try {
      await bridge.removeDemo(jobId)
      listRequest.current += 1
      setAllJobs(current => current.filter(job => job.jobId !== jobId))
      setJobs(current => current.filter(job => job.jobId !== jobId))
      if (localSelectedJobId.current === jobId) {
        localSelectedJobId.current = null
        onLocalJobContextChange?.({ selectedJobId: null, activeArtifactId: null, activeArtifactPage: 1, activeArtifactZoom: 1 })
      }
      if (selectedJobId === jobId) {
        setSelectedJobId(null)
        setSelectedJob(null)
        setSelectedJobDetail(null)
      }
      setFeedback('Fictional demo removed')
      setError(null)
    } catch {
      setError('Demo removal failed')
    }
  }, [bridge, onLocalJobContextChange, selectedJobId])

  const reorder = useCallback(async (jobId: string, direction: -1 | 1) => {
    if (!bridge || sortMode !== 'manual' || query || statusGroup) return
    const index = jobs.findIndex(job => job.jobId === jobId)
    const target = index + direction
    if (index < 0 || target < 0 || target >= jobs.length) return
    const reordered = [...jobs]
    const [moved] = reordered.splice(index, 1)
    if (!moved) return
    reordered.splice(target, 0, moved)
    try {
      await bridge.reorder(reordered.map(job => job.jobId))
      listRequest.current += 1
      setJobs(reordered)
      setAllJobs(reordered)
      setFeedback('Manual order saved')
      setError(null)
    } catch {
      setError('Reordering failed')
    }
  }, [bridge, jobs, query, sortMode, statusGroup])

  const reorderTo = useCallback(async (sourceJobId: string, targetJobId: string) => {
    if (!bridge || sortMode !== 'manual' || query || statusGroup) return
    const sourceIndex = jobs.findIndex(job => job.jobId === sourceJobId)
    const targetIndex = jobs.findIndex(job => job.jobId === targetJobId)
    if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return
    const reordered = [...jobs]
    const [moved] = reordered.splice(sourceIndex, 1)
    if (!moved) return
    reordered.splice(targetIndex, 0, moved)
    try {
      await bridge.reorder(reordered.map(job => job.jobId))
      listRequest.current += 1
      setJobs(reordered)
      setAllJobs(reordered)
      setFeedback('Manual order saved')
      setError(null)
    } catch {
      setError('Reordering failed')
    }
  }, [bridge, jobs, query, sortMode, statusGroup])

  return {
    allJobs,
    jobs,
    selectedJob,
    selectedJobDetail,
    selectedJobId,
    sortMode,
    query,
    statusGroup,
    loading,
    ready,
    error,
    feedback,
    retry,
    setQuery,
    setStatusGroup,
    selectJob,
    selectJobForConversation,
    changeStatus,
    removeDemo,
    changeSort,
    reorder,
    reorderTo
  }
}
