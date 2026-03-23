import api from './client'
import type { VideoAnalysis } from '../types'

export const triggerAnalysis = (projectId: string) =>
  api.post<VideoAnalysis>(`/api/projects/${projectId}/analyze`).then(r => r.data)

export const getAnalysis = (projectId: string) =>
  api.get<VideoAnalysis>(`/api/projects/${projectId}/analysis`).then(r => r.data)
