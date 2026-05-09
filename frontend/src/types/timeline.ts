export interface TalkingHeadTask {
  id: string
  project_id: string
  shot_index: number | null

  model_image_id: string
  model_image_url: string | null
  bg_material_id: string | null
  bg_thumbnail_url: string | null

  composite_status: 'pending' | 'processing' | 'completed' | 'failed'
  composite_preview_url: string | null

  motion_prompt: string | null
  audio_segment_url: string | null
  audio_start_ms: number | null
  audio_end_ms: number | null

  lipsync_status: 'pending' | 'processing' | 'completed' | 'failed'
  video_url: string | null
  thumbnail_url: string | null
  duration_seconds: number | null
  error_message: string | null

  created_at: string
  completed_at: string | null
}

export interface TimelineAsset {
  id: string
  project_id: string
  asset_type: 'video' | 'audio' | 'subtitle'
  filename: string
  file_url: string
  file_size: number
  duration_ms: number | null
}

export interface TimelineClip {
  id: string
  generated_video_id: string | null
  asset_id: string | null
  track_type: 'video' | 'audio' | 'subtitle'
  track_index: number
  position_ms: number
  duration_ms: number
  sort_order: number
  label: string | null
  video_url: string | null
  thumbnail_url: string | null
  filename: string | null
}

export interface Timeline {
  project_id: string
  clips: TimelineClip[]
  assets: TimelineAsset[]
}
