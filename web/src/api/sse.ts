import type { SSEEventPacket } from './types'

export interface SSESubscriptionOptions {
  onEvent: (packet: SSEEventPacket) => void
  onClose?: () => void
  onError?: (err: Error) => void
}

/**
 * 基于 fetch ReadableStream 的 SSE 订阅器，支持所有自定义事件名并可在组件卸载时 Abort。
 */
export function subscribeSessionEvents(
  sessionId: string,
  runId: string,
  opts: SSESubscriptionOptions,
): () => void {
  const controller = new AbortController()
  const url = `/api/sessions/${encodeURIComponent(sessionId)}/events?run_id=${encodeURIComponent(runId)}`

  ;(async () => {
    try {
      const res = await fetch(url, {
        method: 'GET',
        headers: { Accept: 'text/event-stream' },
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        throw new Error(`SSE 连接失败 (${res.status})`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        let sepIdx = buffer.indexOf('\n\n')
        while (sepIdx !== -1) {
          const rawBlock = buffer.slice(0, sepIdx)
          buffer = buffer.slice(sepIdx + 2)
          sepIdx = buffer.indexOf('\n\n')

          let eventName = 'message'
          const dataLines: string[] = []
          for (const line of rawBlock.split('\n')) {
            const trimmed = line.replace(/\r$/, '')
            if (trimmed.startsWith('event:')) {
              eventName = trimmed.slice(6).trim()
            } else if (trimmed.startsWith('data:')) {
              dataLines.push(trimmed.slice(5).trim())
            }
          }
          if (dataLines.length > 0) {
            try {
              const parsed = JSON.parse(dataLines.join('\n')) as Record<string, unknown>
              opts.onEvent({ event: eventName, data: parsed, ts: new Date().toISOString() })
            } catch {
              // skip malformed chunk
            }
          }
        }
      }
      opts.onClose?.()
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      opts.onError?.(err instanceof Error ? err : new Error(String(err)))
    }
  })()

  return () => controller.abort()
}
