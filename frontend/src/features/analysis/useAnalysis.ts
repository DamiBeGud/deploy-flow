import { useEffect, useState } from 'react'
import { ApiError, errorMessage, openEventStream, request, storage } from '../../shared/api'
import { isTerminal } from '../../shared/contracts'
import type { Analysis } from '../../shared/contracts'

const eventTypes = ['queued', 'started', 'checkout_started', 'scanning', 'skills_selected', 'agent_running', 'validating', 'retrying', 'completed', 'needs_input', 'failed', 'cancelled', 'assessment_updated']

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
    let timer: ReturnType<typeof setTimeout> | undefined
    const abort = new AbortController()
    const streamAbort = new AbortController()
    const cursorKey = `events:${runId}`
    let cursor = Number(storage.get(cursorKey) || 0)
    if (!Number.isSafeInteger(cursor) || cursor < 0) cursor = 0

    const load = async (): Promise<boolean> => {
      try {
        const result = await request<Analysis>(`/analyses/${runId}`, { signal: abort.signal })
        if (stopped) return false
        setRun(current => current && isTerminal(current.status) && !isTerminal(result.status) ? current : result); setError('')
        if (isTerminal(result.status)) { streamAbort.abort(); setConnection('Saved'); return false }
        return true
      } catch (e) {
        if (stopped) return false
        setError(errorMessage(e))
        if (e instanceof ApiError && [401, 403, 404].includes(e.status)) { streamAbort.abort(); return false }
        setConnection('Reconnecting'); return true
      }
    }
    const poll = async () => {
      if (await load() && !stopped) timer = setTimeout(poll, 3000)
    }
    const consume = async () => {
      while (!stopped && !streamAbort.signal.aborted) {
        try {
          const response = await openEventStream(`/analyses/${runId}/events?after=${cursor}`, streamAbort.signal)
          if (stopped) return
          setConnection('Live')
          const reader = response.body!.getReader()
          const decoder = new TextDecoder()
          let pending = ''
          while (!stopped && !streamAbort.signal.aborted) {
            const { done, value } = await reader.read()
            if (done) break
            pending += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
            let boundary = pending.indexOf('\n\n')
            while (boundary >= 0) {
              const block = pending.slice(0, boundary)
              pending = pending.slice(boundary + 2)
              const type = block.split('\n').find(line => line.startsWith('event:'))?.slice(6).trim()
              const data = block.split('\n').find(line => line.startsWith('data:'))?.slice(5).trim()
              if (type && data && eventTypes.includes(type.replace(/^analysis\./, ''))) {
                const update = JSON.parse(data) as { event_id: number; message: string; percent: number }
                if (update.event_id > cursor) {
                  cursor = update.event_id
                  storage.set(cursorKey, String(cursor))
                  setMessage(update.message)
                  const phase = type.replace(/^analysis\./, '')
                  setRun(current => current && !isTerminal(current.status) ? {
                    ...current, progress: update.percent,
                    status: phase === 'retrying' || phase === 'queued' ? 'queued' : 'running',
                  } : current)
                  if (['completed', 'needs_input', 'failed', 'cancelled'].includes(phase)) void load()
                }
              }
              boundary = pending.indexOf('\n\n')
            }
            if (pending.length > 65536) throw new Error('Analysis event exceeds the allowed size.')
          }
        } catch (e) {
          if (stopped || streamAbort.signal.aborted) return
          setConnection('Reconnecting')
          if (e instanceof ApiError && [401, 403, 404].includes(e.status)) { setError(errorMessage(e)); return }
        }
        if (!stopped && !streamAbort.signal.aborted) {
          setConnection('Reconnecting')
          await new Promise(resolve => setTimeout(resolve, 1000))
        }
      }
    }
    const start = async () => {
      if (!await load() || stopped) return
      void consume()
      timer = setTimeout(poll, 3000)
    }
    void start()
    return () => { stopped = true; abort.abort(); streamAbort.abort(); clearTimeout(timer) }
  }, [runId, refresh])
  return { run, error, message, connection, reload: () => setRefresh(n => n + 1) }
}
