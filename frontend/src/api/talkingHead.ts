import api from './client'
import type { ModelImage, TalkingHeadTask } from '../types'

// ── Model Image APIs ──────────────────────────────────────────────────

export const uploadModelImage = (projectId: string, file: File) => {
  const formData = new FormData()
  formData.append('file', file)
  return api.post<ModelImage>(`/api/projects/${projectId}/model-images`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export const getModelImages = (projectId: string) =>
  api.get<ModelImage[]>(`/api/projects/${projectId}/model-images`).then(r => r.data)

export const deleteModelImage = (id: string) =>
  api.delete(`/api/model-images/${id}`)

// ── Audio Upload APIs ─────────────────────────────────────────────────

export interface AudioUploadResult {
  id: string
  filename: string
  file_url: string
  file_size: number
}

export const uploadAudio = (projectId: string, file: File) => {
  const formData = new FormData()
  formData.append('file', file)
  return api.post<AudioUploadResult>(`/api/projects/${projectId}/talking-head-audio`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

// ── Talking Head Task APIs ────────────────────────────────────────────

export const createTalkingHeadTask = (
  projectId: string,
  data: {
    model_image_id: string
    bg_material_id?: string | null
    shot_index?: number | null
    motion_prompt?: string | null
    audio_segment_url?: string | null
    audio_start_ms?: number | null
    audio_end_ms?: number | null
  },
) => api.post<TalkingHeadTask>(`/api/projects/${projectId}/talking-head`, data).then(r => r.data)

export const getTalkingHeadTasks = (projectId: string) =>
  api.get<TalkingHeadTask[]>(`/api/projects/${projectId}/talking-head`).then(r => r.data)

export const getTalkingHeadTask = (taskId: string) =>
  api.get<TalkingHeadTask>(`/api/talking-head/${taskId}`).then(r => r.data)

export const triggerComposite = (taskId: string) =>
  api.post<TalkingHeadTask>(`/api/talking-head/${taskId}/composite`).then(r => r.data)

export const updatePromptAndAudio = (
  taskId: string,
  data: {
    motion_prompt?: string | null
    audio_segment_url?: string | null
    audio_start_ms?: number | null
    audio_end_ms?: number | null
  },
) => api.patch<TalkingHeadTask>(`/api/talking-head/${taskId}/prompt`, data).then(r => r.data)

export const triggerLipSync = (taskId: string) =>
  api.post<TalkingHeadTask>(`/api/talking-head/${taskId}/generate`).then(r => r.data)
