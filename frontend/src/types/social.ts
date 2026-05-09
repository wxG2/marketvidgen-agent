export interface SocialAccount {
  id: string
  user_id: string
  platform: string
  open_id: string
  display_name: string | null
  avatar_url: string | null
  expires_at: string | null
  scopes: string[]
  status: string
  is_default: boolean
  last_synced_at: string | null
  created_at: string
  updated_at: string
}

export interface PublishDraft {
  platform: string
  pipeline_run_id: string
  delivery_record_id?: string | null
  social_account_id?: string | null
  account_name?: string | null
  title: string
  description: string
  hashtags: string[]
  visibility: string
  cover_title?: string | null
  topic?: string | null
  risk_tip?: string | null
  video_source?: string | null
  status: string
}
