import api from './client'
import type {
  AdminApiKeyCreateResult,
  AdminApiKeyRecord,
  ApiKeyCreateResult,
  ApiKeyRecord,
  ApiKeyScope,
} from '../types'

export const listApiKeys = () =>
  api.get<ApiKeyRecord[]>('/api/api-keys').then((r) => r.data)

export const createApiKey = (payload: { name: string; scopes: ApiKeyScope[] }) =>
  api.post<ApiKeyCreateResult>('/api/api-keys', payload).then((r) => r.data)

export const disableApiKey = (apiKeyId: string) =>
  api.post<ApiKeyRecord>(`/api/api-keys/${apiKeyId}/disable`).then((r) => r.data)

export const listAdminApiKeys = () =>
  api.get<AdminApiKeyRecord[]>('/api/admin/api-keys').then((r) => r.data)

export const createAdminApiKey = (payload: { user_id: string; name: string; scopes: ApiKeyScope[] }) =>
  api.post<AdminApiKeyCreateResult>('/api/admin/api-keys', payload).then((r) => r.data)

export const disableAdminApiKey = (apiKeyId: string) =>
  api.post<AdminApiKeyRecord>(`/api/admin/api-keys/${apiKeyId}/disable`).then((r) => r.data)
