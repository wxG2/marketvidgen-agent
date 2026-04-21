import api from './client'
import type { RepositoryAsset, RepositoryUpload, RepositoryDelivery, VideoUpload } from '../types'

export const listUserUploads = () =>
  api.get<RepositoryUpload[]>('/api/repository/uploads').then(r => r.data)

export const importRepositoryUpload = (uploadId: string, projectId: string, sessionId?: string | null) =>
  api.post<VideoUpload>(`/api/repository/uploads/${uploadId}/import`, null, {
    params: {
      project_id: projectId,
      session_id: sessionId || undefined,
    },
  }).then(r => r.data)

export const deleteUserUpload = (uploadId: string) =>
  api.delete<{ ok: boolean }>(`/api/repository/uploads/${uploadId}`).then(r => r.data)

export const listUserDeliveries = () =>
  api.get<RepositoryDelivery[]>('/api/repository/deliveries').then(r => r.data)

export const listRepositoryAssets = () =>
  api.get<RepositoryAsset[]>('/api/repository/assets').then(r => r.data)

export const importRepositoryDelivery = (deliveryId: string, projectId: string, sessionId?: string | null) =>
  api.post<VideoUpload>(`/api/repository/deliveries/${deliveryId}/import`, null, {
    params: {
      project_id: projectId,
      session_id: sessionId || undefined,
    },
  }).then(r => r.data)
