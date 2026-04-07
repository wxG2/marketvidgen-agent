import api from './client'
import type { BackgroundTemplate, BackgroundTemplateKeywordDraft, BackgroundTemplateLearningLog } from '../types'

export const listBackgroundTemplates = () =>
  api.get<BackgroundTemplate[]>('/api/background-templates').then(r => r.data)

export const createBackgroundTemplate = (payload: Partial<BackgroundTemplate>) =>
  api.post<BackgroundTemplate>('/api/background-templates', payload).then(r => r.data)

export const updateBackgroundTemplate = (id: string, payload: Partial<BackgroundTemplate>) =>
  api.patch<BackgroundTemplate>(`/api/background-templates/${id}`, payload).then(r => r.data)

export const deleteBackgroundTemplate = (id: string) =>
  api.delete(`/api/background-templates/${id}`).then(r => r.data)

export const getBackgroundTemplateLearningLogs = (id: string) =>
  api.get<BackgroundTemplateLearningLog[]>(`/api/background-templates/${id}/learning-logs`).then(r => r.data)

export const importPresetBackgroundTemplates = () =>
  api.post<BackgroundTemplate[]>('/api/background-templates/import-presets').then(r => r.data)

export const generateBackgroundTemplateFromKeywords = (payload: { keywords: string; template_id?: string | null }) =>
  api.post<BackgroundTemplateKeywordDraft>('/api/background-templates/generate-from-keywords', payload).then(r => r.data)
