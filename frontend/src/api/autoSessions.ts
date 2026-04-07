import api from './client'
import type {
  AutoChatSessionMessage,
  AutoChatMessagePayload,
  AutoChatSessionDetail,
  AutoChatSessionSummary,
  MaterialSelection,
  PublishDraft,
} from '../types'

export const listAutoSessions = (projectId: string) =>
  api.get<AutoChatSessionSummary[]>(`/api/projects/${projectId}/auto-sessions`).then(r => r.data)

export const createAutoSession = (projectId: string) =>
  api.post<AutoChatSessionDetail>(`/api/projects/${projectId}/auto-sessions`).then(r => r.data)

export const getAutoSession = (projectId: string, sessionId: string) =>
  api.get<AutoChatSessionDetail>(`/api/projects/${projectId}/auto-sessions/${sessionId}`).then(r => r.data)

export const updateAutoSession = (
  projectId: string,
  sessionId: string,
  payload: Partial<{
    title: string
    status_preview: string
    draft_script: string | null
    background_template_id: string | null
    reference_video_id: string | null
    video_platform: string
    video_no_audio: boolean
    duration_mode: string
    video_transition: string
    bgm_mood: string
    watermark_id: string | null
    current_run_id: string | null
    last_activity_at: string
  }>,
) => api.patch<AutoChatSessionDetail>(`/api/projects/${projectId}/auto-sessions/${sessionId}`, payload).then(r => r.data)

export const appendAutoSessionMessage = (
  projectId: string,
  sessionId: string,
  payload: {
    role: 'user' | 'assistant' | 'system'
    title?: string
    content: string
    payload?: AutoChatMessagePayload
  },
) => api.post<AutoChatSessionMessage>(`/api/projects/${projectId}/auto-sessions/${sessionId}/messages`, payload).then(r => r.data)

export const updateAutoSessionMessage = (
  projectId: string,
  sessionId: string,
  messageId: string,
  payload: {
    title?: string
    content?: string
    payload?: AutoChatMessagePayload
  },
) => api.patch<AutoChatSessionMessage>(`/api/projects/${projectId}/auto-sessions/${sessionId}/messages/${messageId}`, payload).then(r => r.data)

export const listAutoSessionMaterials = (projectId: string, sessionId: string) =>
  api.get<MaterialSelection[]>(`/api/projects/${projectId}/auto-sessions/${sessionId}/materials`).then(r => r.data)

export const selectAutoSessionMaterial = (
  projectId: string,
  sessionId: string,
  materialId: string,
  category: string,
  sortOrder = 0,
) =>
  api.post<MaterialSelection>(`/api/projects/${projectId}/auto-sessions/${sessionId}/materials`, {
    material_id: materialId,
    category,
    sort_order: sortOrder,
  }).then(r => r.data)

export const deselectAutoSessionMaterial = (projectId: string, sessionId: string, materialId: string) =>
  api.delete(`/api/projects/${projectId}/auto-sessions/${sessionId}/materials/${materialId}`).then(r => r.data)

export const createAutoSessionPublishDraft = (
  projectId: string,
  sessionId: string,
  payload?: { platform?: string; social_account_id?: string | null },
) =>
  api.post<PublishDraft>(`/api/projects/${projectId}/auto-sessions/${sessionId}/publish-drafts`, payload || {}).then(r => r.data)
