export interface RepositoryUpload {
  id: string
  project_id: string
  project_name: string
  filename: string
  file_size: number
  duration_seconds: number | null
  mime_type: string | null
  stream_url: string
  created_at: string
}

export interface RepositoryDelivery {
  id: string
  project_id: string
  project_name: string
  pipeline_run_id: string
  title: string | null
  description: string | null
  status: string
  video_url: string | null
  created_at: string
}

export interface RepositoryAsset {
  id: string
  user_id: string
  project_id: string
  project_name?: string | null
  pipeline_run_id: string
  asset_key: string
  asset_type: string
  source_agent: string
  title: string | null
  description: string | null
  mime_type: string | null
  file_path: string | null
  file_url: string | null
  file_size: number | null
  text_content: string | null
  metadata: Record<string, unknown>
  duration_ms: number | null
  created_at: string
  updated_at: string
}
