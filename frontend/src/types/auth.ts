export interface AuthUser {
  id: string
  username: string
  role: 'admin' | 'user'
  is_active: boolean
  created_at: string
}

export type ApiKeyScope = 'video_jobs:create' | 'video_jobs:read' | 'video_jobs:review' | '*'

export interface ApiKeyRecord {
  id: string
  name: string
  key_prefix: string
  status: string
  scopes: ApiKeyScope[]
  last_used_at: string | null
  created_at: string
}

export interface ApiKeyCreateResult extends ApiKeyRecord {
  api_key: string
}

export interface AdminApiKeyRecord extends ApiKeyRecord {
  user_id: string
}

export interface AdminApiKeyCreateResult extends AdminApiKeyRecord {
  api_key: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  password: string
}
