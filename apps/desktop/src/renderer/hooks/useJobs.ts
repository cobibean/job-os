import { useCallback, useEffect, useRef, useState } from 'react'

import type { JobDetail, JobListItem, JobSortMode, JobStatus } from '../../shared/contracts'

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

export function useJobs() {
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
  const detailRequest = useRef(0)
  const selectionRevision = useRef(0)
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
    try {
      const refreshed = await bridge.list(
        sortMode,
        query.trim() || undefined,
        statusGroup || undefined
      )
      setJobs(refreshed)
      setSelectedJobId(selectedId => {
        setSelectedJob(current => selectedId
          ? refreshed.find(job => job.jobId === selectedId) ?? current
          : null)
        return selectedId
      })
      setError(null)
    } catch {
      setError('Jobs unavailable')
    }
  }, [bridge, query, sortMode, statusGroup])

  useEffect(() => {
    if (!bridge) return
    const unsubscribe = bridge.subscribe(event => {
      if (event.eventType === 'job_selected' && event.jobId) {
        selectionRevision.current += 1
        setSelectedJobId(event.jobId)
        setSelectedJob(current => (
          jobs.find(job => job.jobId === event.jobId) ?? current
        ))
        void loadDetail(event.jobId)
      } else if (event.eventType === 'job_description_updated' && event.jobId === selectedJobId) {
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
  }, [bridge, jobs, loadDetail, refresh, selectedJobId])

  useEffect(() => {
    if (!bridge) {
      setLoading(false)
      return
    }
    let active = true
    const initialSelectionRevision = selectionRevision.current
    bridge.getState().then(snapshot => {
      if (!active) return
      setJobs(snapshot.jobs)
      if (selectionRevision.current === initialSelectionRevision) {
        setSelectedJobId(snapshot.selectedJobId)
        setSelectedJob(
          snapshot.jobs.find(job => job.jobId === snapshot.selectedJobId) ?? null
        )
        void loadDetail(snapshot.selectedJobId)
      }
      setSortMode(snapshot.sortMode)
      setLoading(false)
      setReady(true)
    }).catch(() => {
      if (!active) return
      setError('Jobs unavailable')
      setLoading(false)
    })
    return () => { active = false }
  }, [bridge, loadDetail])

  useEffect(() => {
    if (!bridge || !ready) return
    const timeout = window.setTimeout(() => { void refresh() }, 120)
    return () => window.clearTimeout(timeout)
  }, [bridge, ready, refresh])

  const selectJob = useCallback(async (jobId: string) => {
    if (!bridge) return false
    const requestRevision = selectionRevision.current + 1
    selectionRevision.current = requestRevision
    try {
      await bridge.select(jobId)
      const [snapshot, refreshed] = await Promise.all([
        bridge.getState(),
        bridge.list(sortMode, query.trim() || undefined, statusGroup || undefined)
      ])
      if (selectionRevision.current !== requestRevision) return true
      setJobs(refreshed)
      setSelectedJobId(jobId)
      setSelectedJob(snapshot.jobs.find(job => job.jobId === jobId) ?? null)
      setSortMode(snapshot.sortMode)
      setFeedback('Active job selected')
      setError(null)
      await loadDetail(jobId)
      return true
    } catch {
      if (selectionRevision.current === requestRevision) setError('Selection failed')
      return false
    }
  }, [bridge, loadDetail, query, sortMode, statusGroup])

  const changeStatus = useCallback(async (jobId: string, status: JobStatus) => {
    if (!bridge) return
    try {
      const result = await bridge.updateStatus(jobId, status)
      setJobs(current => current.map(job => job.jobId === jobId ? result.job : job))
      setSelectedJob(current => current?.jobId === jobId ? result.job : current)
      if (selectedJobId === jobId) void loadDetail(jobId)
      setFeedback(`Status changed to ${status}`)
      setError(null)
    } catch (statusError) {
      setError(statusChangeError(statusError))
    }
  }, [bridge, loadDetail, selectedJobId])

  const changeSort = useCallback(async (sort: JobSortMode) => {
    if (!bridge) return
    try {
      await bridge.setSort(sort)
      setSortMode(sort)
      setFeedback(`Ordered by ${sort}`)
      setError(null)
    } catch {
      setError('Ordering failed')
    }
  }, [bridge])

  const removeDemo = useCallback(async (jobId: string) => {
    if (!bridge) return
    try {
      await bridge.removeDemo(jobId)
      setJobs(current => current.filter(job => job.jobId !== jobId))
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
  }, [bridge, selectedJobId])

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
      setJobs(reordered)
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
      setJobs(reordered)
      setFeedback('Manual order saved')
      setError(null)
    } catch {
      setError('Reordering failed')
    }
  }, [bridge, jobs, query, sortMode, statusGroup])

  return {
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
    setQuery,
    setStatusGroup,
    selectJob,
    changeStatus,
    removeDemo,
    changeSort,
    reorder,
    reorderTo
  }
}
