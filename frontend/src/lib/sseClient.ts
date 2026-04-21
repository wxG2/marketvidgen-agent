export interface SSEStreamHandlers {
  onText?: (payload: unknown) => void
  onStatus?: (payload: unknown) => void
  onToolCall?: (payload: unknown) => void
  onToolResult?: (payload: unknown) => void
  onDone?: (payload: unknown) => void
  onError?: (error: Error) => void
}

export interface SSEStreamHandle {
  close: () => void
}

export interface SSEStreamOptions {
  signal?: AbortSignal
}

function parseEventData(raw: string) {
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return raw
  }
}

function dispatchEvent(eventName: string, rawData: string, handlers: SSEStreamHandlers) {
  const payload = parseEventData(rawData)
  if (eventName === 'text') handlers.onText?.(payload)
  else if (eventName === 'status') handlers.onStatus?.(payload)
  else if (eventName === 'tool_call') handlers.onToolCall?.(payload)
  else if (eventName === 'tool_result') handlers.onToolResult?.(payload)
  else if (eventName === 'done') handlers.onDone?.(payload)
}

function processChunk(chunk: string, handlers: SSEStreamHandlers) {
  let eventName = 'message'
  const dataLines: string[] = []

  for (const rawLine of chunk.split('\n')) {
    const line = rawLine.trimEnd()
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (dataLines.length > 0) {
    dispatchEvent(eventName, dataLines.join('\n'), handlers)
  }
}

export async function createSSEStream(
  url: string,
  body: object,
  handlers: SSEStreamHandlers,
  options: SSEStreamOptions = {},
): Promise<SSEStreamHandle> {
  const controller = new AbortController()
  const externalSignal = options.signal
  let cleanupExternalAbort = () => {}

  if (externalSignal) {
    const abortFromExternalSignal = () => controller.abort()
    if (externalSignal.aborted) {
      controller.abort()
    } else {
      externalSignal.addEventListener('abort', abortFromExternalSignal, { once: true })
      cleanupExternalAbort = () => externalSignal.removeEventListener('abort', abortFromExternalSignal)
    }
  }

  let response: Response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify(body),
      signal: controller.signal,
    })
  } catch (error) {
    cleanupExternalAbort()
    throw error
  }

  if (!response.ok || !response.body) {
    const detail = await response.text()
    cleanupExternalAbort()
    throw new Error(detail || 'SSE 连接失败')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()

  ;(async () => {
    let buffer = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

        let boundaryIndex = buffer.indexOf('\n\n')
        while (boundaryIndex >= 0) {
          const rawChunk = buffer.slice(0, boundaryIndex)
          buffer = buffer.slice(boundaryIndex + 2)
          processChunk(rawChunk, handlers)
          boundaryIndex = buffer.indexOf('\n\n')
        }
      }

      const tail = buffer.trim()
      if (tail) processChunk(tail, handlers)
    } catch (error) {
      if (controller.signal.aborted) return
      handlers.onError?.(error instanceof Error ? error : new Error('SSE 读取失败'))
    } finally {
      cleanupExternalAbort()
    }
  })().catch((error) => {
    cleanupExternalAbort()
    handlers.onError?.(error instanceof Error ? error : new Error('SSE 读取失败'))
  })

  return {
    close: () => {
      cleanupExternalAbort()
      controller.abort()
      reader.cancel().catch(() => undefined)
    },
  }
}
