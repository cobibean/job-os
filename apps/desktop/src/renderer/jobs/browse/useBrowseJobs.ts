import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { JobDetail, JobListItem, JobSortMode, JobStatus } from '../../../shared/contracts'
import { CANONICAL_STATUS_GROUPS, STATUS_TRANSITIONS } from '../jobStatus'

const groupOrder = new Map<string, number>(CANONICAL_STATUS_GROUPS.map((group, index) => [group, index]))

export function filterAndSortBrowseJobs(
  jobs: JobListItem[], query: string, statusGroup: string, sortMode: JobSortMode
) {
  const needle = query.trim().toLocaleLowerCase()
  const filtered = jobs.filter(job => (
    (!statusGroup || job.statusGroup === statusGroup)
    && (!needle || `${job.company} ${job.title}`.toLocaleLowerCase().includes(needle))
  ))
  if (sortMode === 'manual') return filtered
  return [...filtered].sort((left, right) => {
    if (sortMode === 'recent') return right.lastSeenAt.localeCompare(left.lastSeenAt)
    if (sortMode === 'alphabetical') {
      return left.company.localeCompare(right.company) || left.title.localeCompare(right.title)
    }
    return (groupOrder.get(left.statusGroup) ?? 99) - (groupOrder.get(right.statusGroup) ?? 99)
      || left.company.localeCompare(right.company)
  })
}

export function useBrowseJobs(options: {
  active: boolean
  activeJobId: string | null
  persistedFocusJobId: string | null
  query: string
  statusGroup: string
  sortMode: JobSortMode
  onFocusChange: (jobId: string | null, message?: string) => void
}) {
  const bridge = useRef(window.jobos?.jobs).current
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [loading, setLoading] = useState(Boolean(bridge))
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detailJobId, setDetailJobId] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [mutationError, setMutationError] = useState<string | null>(null)
  const detailRequest = useRef(0)
  const jobsRequest = useRef(0)
  const eventRefreshTimer = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    if (!bridge) {
      setLoading(false)
      return
    }
    const request = jobsRequest.current + 1
    jobsRequest.current = request
    try {
      const refreshed = await bridge.list('manual')
      if (request !== jobsRequest.current) return
      setJobs(refreshed)
      setError(null)
    } catch {
      if (request === jobsRequest.current) setError('Jobs unavailable')
    } finally {
      if (request === jobsRequest.current) setLoading(false)
    }
  }, [bridge])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!bridge) return
    const unsubscribe = bridge.subscribe(() => {
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
  }, [bridge, refresh])

  const results = useMemo(
    () => filterAndSortBrowseJobs(jobs, options.query, options.statusGroup, options.sortMode),
    [jobs, options.query, options.sortMode, options.statusGroup]
  )

  const focusJobId = results.some(job => job.jobId === options.persistedFocusJobId)
    ? options.persistedFocusJobId
    : results.some(job => job.jobId === options.activeJobId)
      ? options.activeJobId
      : results[0]?.jobId ?? null

  useEffect(() => {
    if (!loading && focusJobId !== options.persistedFocusJobId) {
      options.onFocusChange(focusJobId)
    }
  }, [focusJobId, loading, options.onFocusChange, options.persistedFocusJobId])

  useEffect(() => {
    const request = detailRequest.current + 1
    detailRequest.current = request
    setDetail(null)
    setDetailJobId(focusJobId)
    setDetailError(null)
    if (!options.active || !focusJobId || !bridge) {
      setDetailLoading(false)
      return
    }
    setDetailLoading(true)
    void bridge.inspect(focusJobId).then(next => {
      if (request === detailRequest.current) setDetail(next)
    }).catch(() => {
      if (request === detailRequest.current) setDetailError('Job detail unavailable')
    }).finally(() => {
      if (request === detailRequest.current) setDetailLoading(false)
    })
  }, [bridge, focusJobId, options.active])

  const changeStatus = useCallback(async (jobId: string, status: JobStatus) => {
    if (!bridge) return
    const job = jobs.find(candidate => candidate.jobId === jobId)
    if (!job || !STATUS_TRANSITIONS[job.status].includes(status)) return
    const request = jobsRequest.current + 1
    jobsRequest.current = request
    try {
      const result = await bridge.updateStatus(jobId, status)
      if (request !== jobsRequest.current) return
      setJobs(current => current.map(candidate => candidate.jobId === jobId ? result.job : candidate))
      setMutationError(null)
    } catch {
      if (request === jobsRequest.current) setMutationError('Status change failed')
    }
  }, [bridge, jobs])

  return { jobs, results, focusJobId, detail, detailJobId, loading, detailLoading, error, detailError, mutationError, changeStatus }
}
