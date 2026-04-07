import api from './client'
import type { RepositoryUpload, RepositoryDelivery } from '../types'

export const listUserUploads = () =>
  api.get<RepositoryUpload[]>('/api/repository/uploads').then(r => r.data)

export const deleteUserUpload = (uploadId: string) =>
  api.delete<{ ok: boolean }>(`/api/repository/uploads/${uploadId}`).then(r => r.data)

export const listUserDeliveries = () =>
  api.get<RepositoryDelivery[]>('/api/repository/deliveries').then(r => r.data)
