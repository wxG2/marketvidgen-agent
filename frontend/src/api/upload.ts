import api from './client'
import type { VideoUpload } from '../types'

export const uploadVideo = (projectId: string, file: File, onProgress?: (pct: number) => void) =>
  api.post<VideoUpload>(`/api/projects/${projectId}/upload`, (() => {
    const fd = new FormData()
    fd.append('file', file)
    return fd
  })(), {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    },
  }).then(r => r.data)

export const getUpload = (projectId: string) =>
  api.get<VideoUpload>(`/api/projects/${projectId}/upload`).then(r => r.data)

export const getVideoStreamUrl = (uploadId: string) =>
  `/api/uploads/${uploadId}/stream`
