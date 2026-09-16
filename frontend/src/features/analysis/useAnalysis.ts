import { useEffect, useState } from 'react'
import { ApiError, errorMessage, request, storage } from '../../shared/api'
import { isTerminal } from '../../shared/contracts'
import type { Analysis } from '../../shared/contracts'

const eventTypes = ['queued', 'started', 'scanning', 'agent_running', 'validating', 'retrying', 'completed', 'failed', 'cancelled', 'assessment_updated']

export function useAnalysis(runId: string | null) {
  const [run, setRun] = useState<Analysis | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [connection, setConnection] = useState('Loading')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    // A new run/reload replaces the previous external stream and its snapshot.
    // eslint-disable-next-line react/set-state-in-effect
    setRun(null); setError(''); setMessage(''); setConnection('Loading')
    if (!runId) return
    let stopped = false
    let source: EventSource | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    const abort = new AbortController()
    const cursorKey = `events:${runId}`
    let cursor = Number(storage.get(cursorKey) || 0)
    if (!Number.isSafeInteger(cursor) || cursor < 0) cursor = 0

    const load = async (): Promise<boolean> => {
      try {
        const result = await request<Analysis>(`/analyses/${runId}`, { signal: abort.signal })
        if (stopped) return false
        setRun(current => current && isTerminal(current.status) && !isTerminal(result.status) ? current : result); setError('')
        if (isTerminal(result.status)) { source?.close(); setConnection('Saved'); return false }
        return true
      } catch (e) {
        if (stopped) return false
        setError(errorMessage(e))
        if (e instanceof ApiError && [403, 404].includes(e.status)) { source?.close(); return false }
        setConnection('Reconnecting'); return true
      }
    }
    const poll = async () => {
      if (await load() && !stopped) timer = setTimeout(poll, 3000)
    }
    const start = async () => {
      if (!await load() || stopped) return
      source = new EventSource(`/api/v1/analyses/${runId}/events?after=${cursor}`)
      source.onopen = () => { if (!stopped) setConnection('Live') }
      source.onerror = () => { if (!stopped) setConnection('Reconnecting') }
      for (const type of eventTypes) source.addEventListener(`analysis.${type}`, event => {
        if (stopped) return
        const update = JSON.parse((event as MessageEvent).data) as { event_id: number; message: string; percent: number }
        if (update.event_id <= cursor) return
        cursor = update.event_id
        storage.set(cursorKey, String(cursor))
        setMessage(update.message)
        setRun(current => current && !isTerminal(current.status) ? {
          ...current, progress: update.percent,
          status: type === 'retrying' || type === 'queued' ? 'queued' : 'running',
        } : current)
        if (['completed', 'failed', 'cancelled'].includes(type)) { source?.close(); void load() }
      })
      timer = setTimeout(poll, 3000)
    }
    void start()
    return () => { stopped = true; abort.abort(); source?.close(); clearTimeout(timer) }
  }, [runId, refresh])
  return { run, error, message, connection, reload: () => setRefresh(n => n + 1) }
}
