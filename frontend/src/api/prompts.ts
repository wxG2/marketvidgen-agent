import api from './client'
import type { ChatMessage, PromptTemplate, Prompt, PromptBinding } from '../types'

export const getTemplates = () =>
  api.get<PromptTemplate[]>('/api/prompts/templates').then(r => r.data)

export const getChatHistory = (projectId: string) =>
  api.get<ChatMessage[]>(`/api/projects/${projectId}/chat`).then(r => r.data)

export const sendChatMessage = async (projectId: string, content: string, onChunk: (text: string) => void) => {
  const response = await fetch(`/api/projects/${projectId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  const reader = response.body?.getReader()
  if (!reader) return
  const decoder = new TextDecoder()
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    const text = decoder.decode(value)
    for (const line of text.split('\n')) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6))
          onChunk(data.content)
        } catch { /* skip */ }
      }
    }
  }
}

export const generatePrompts = (projectId: string) =>
  api.post<Prompt[]>(`/api/projects/${projectId}/prompts/generate`).then(r => r.data)

export const getPrompts = (projectId: string) =>
  api.get<Prompt[]>(`/api/projects/${projectId}/prompts`).then(r => r.data)

export const updatePrompt = (projectId: string, promptId: string, text: string) =>
  api.patch<Prompt>(`/api/projects/${projectId}/prompts/${promptId}`, { prompt_text: text }).then(r => r.data)

export const getPromptBindings = (projectId: string) =>
  api.get<PromptBinding[]>(`/api/projects/${projectId}/prompt-bindings`).then(r => r.data)
