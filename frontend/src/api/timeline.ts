import api from './client'
import type { Timeline, TimelineAsset } from '../types'

export const getTimeline = (projectId: string) =>
  api.get<Timeline>(`/api/projects/${projectId}/timeline`).then(r => r.data)

export const saveTimeline = (projectId: string, clips: Array<{
  generated_video_id?: string | null
  asset_id?: string | null
  track_type: string
  track_index: number
  position_ms: number
  duration_ms: number
  sort_order: number
  label?: string | null
}>) =>
  api.put<Timeline>(`/api/projects/${projectId}/timeline`, { clips }).then(r => r.data)

export const uploadTimelineAsset = (projectId: string, file: File, onProgress?: (pct: number) => void) => {
  const fd = new FormData()
  fd.append('file', file)
  return api.post<TimelineAsset>(`/api/projects/${projectId}/timeline/assets`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    },
  }).then(r => r.data)
}

export const deleteTimelineAsset = (assetId: string) =>
  api.delete(`/api/timeline/assets/${assetId}`).then(r => r.data)
