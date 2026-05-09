export type ChatEventType = 'reasoning' | 'tool_call' | 'tool_result' | 'tool_progress' | 'error' | 'done'

export interface ChatStreamEvent {
  event: ChatEventType
  data: Record<string, unknown>
}

export interface ToolCallInfo {
  tool_name: string
  input: Record<string, unknown>
  call_id: string
  result?: string
  media_urls?: string[]
  status: 'running' | 'completed' | 'failed'
}

export interface ChatMessageItem {
  id: string
  role: 'user' | 'assistant'
  content: string
  tool_calls?: ToolCallInfo[]
  timestamp: number
}
