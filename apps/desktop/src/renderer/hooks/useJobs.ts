import { useCallback, useEffect, useRef, useState } from 'react'

import type { JobListItem, JobSortMode, JobStatus } from '../../shared/contracts'

export function useJobs() {
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [selectedJob, setSelectedJob] = useState<JobListItem | null>(null)
  const [sortMode, setSortMode] = useState<JobSortMode>('manual')
  const [query, setQuery] = useState('')
  const [statusGroup, setStatusGroup] = useState('')
  const [loading, setLoading] = useState(true)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)

  const bridge = useRef(window.jobos?.jobs).current

  const refresh = useCallback(async () => {
    if (!bridge) return
    try {
      const refreshed = await bridge.list(
        sortMode,
        query.trim() || undefined,
        statusGroup || undefined
      )
      setJobs(refreshed)
      setSelectedJob(current => {
        if (!current) return current
        return refreshed.find(job => job.jobId === current.jobId) ?? current
      })
      setError(null)
    } catch {
      setError('Jobs unavailable')
    }
  }, [bridge, query, sortMode, statusGroup])

  useEffect(() => {
    if (!bridge) {
      setLoading(false)
      return
    }
    let active = true
    bridge.getState().then(snapshot => {
      if (!active) return
      setJobs(snapshot.jobs)
      setSelectedJobId(snapshot.selectedJobId)
      setSelectedJob(
        snapshot.jobs.find(job => job.jobId === snapshot.selectedJobId) ?? null
      )
      setSortMode(snapshot.sortMode)
      setLoading(false)
      setReady(true)
    }).catch(() => {
      if (!active) return
      setError('Jobs unavailable')
      setLoading(false)
    })
    return () => { active = false }
  }, [bridge])

  useEffect(() => {
    if (!bridge || !ready) return
    const timeout = window.setTimeout(() => { void refresh() }, 120)
    return () => window.clearTimeout(timeout)
  }, [bridge, ready, refresh])

  useEffect(() => {
    if (!bridge) return
    return bridge.subscribe(event => {
      if (event.eventType === 'job_selected' && event.jobId) {
        setSelectedJobId(event.jobId)
        setSelectedJob(current => (
          jobs.find(job => job.jobId === event.jobId) ?? current
        ))
      }
      setFeedback(event.origin === 'mcp' ? 'Agent changes synced' : 'Job changes synced')
      void refresh()
    })
  }, [bridge, jobs, refresh])

  const selectJob = useCallback(async (jobId: string) => {
    if (!bridge) return
    try {
      await bridge.select(jobId)
      setSelectedJobId(jobId)
      setSelectedJob(jobs.find(job => job.jobId === jobId) ?? null)
      setFeedback('Active job selected')
      setError(null)
    } catch {
      setError('Selection failed')
    }
  }, [bridge, jobs])

  const changeStatus = useCallback(async (jobId: string, status: JobStatus) => {
    if (!bridge) return
    try {
      const result = await bridge.updateStatus(jobId, status)
      setJobs(current => current.map(job => job.jobId === jobId ? result.job : job))
      setSelectedJob(current => current?.jobId === jobId ? result.job : current)
      setFeedback(`Status changed to ${status}`)
      setError(null)
    } catch {
      setError('Status change failed')
    }
  }, [bridge])

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
    selectedJobId,
    sortMode,
    query,
    statusGroup,
    loading,
    error,
    feedback,
    setQuery,
    setStatusGroup,
    selectJob,
    changeStatus,
    changeSort,
    reorder,
    reorderTo
  }
}
