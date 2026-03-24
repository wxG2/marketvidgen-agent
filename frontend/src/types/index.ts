export interface Project {
  id: string
  name: string
  current_step: number
  created_at: string
  updated_at: string
}

export interface VideoUpload {
  id: string
  project_id: string
  filename: string
  file_size: number
  duration_seconds: number | null
  mime_type: string | null
  created_at: string
}

export interface VideoAnalysis {
  id: string
  project_id: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  summary: string | null
  scene_tags: string[] | null
  recommended_categories: string[] | null
  error_message: string | null
  created_at: string
  completed_at: string | null
}

export interface MaterialItem {
  id: string
  category: string
  filename: string
  media_type: string
  file_size: number | null
  width: number | null
  height: number | null
  thumbnail_url: string | null
}

export interface MaterialCategory {
  name: string
  count: number
}

export interface MaterialSelection {
  id: string
  material_id: string
  category: string
  sort_order: number
  material: MaterialItem | null
}

export interface MaterialsPage {
  items: MaterialItem[]
  total: number
  page: number
  page_size: number
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
}

export interface PromptTemplate {
  name: string
  description: string
  template: string
}

export interface Prompt {
  id: string
  project_id: string
  material_selection_id: string | null
  prompt_text: string
  created_at: string
}

export interface PromptBinding {
  prompt_id: string
  prompt_text: string
  material_id: string | null
  material_filename: string | null
  material_category: string | null
  material_thumbnail_url: string | null
}

export interface ModelImage {
  id: string
  project_id: string
  filename: string
  file_url: string
  width: number | null
  height: number | null
  created_at: string
}

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

export interface GeneratedVideo {
  id: string
  project_id: string
  prompt_id: string
  material_id: string | null
  status: 'pending' | 'processing' | 'completed' | 'failed'
  video_url: string | null
  thumbnail_url: string | null
  duration_seconds: number | null
  is_selected: boolean
  error_message: string | null
  created_at: string
  completed_at: string | null
  // Bound info
  prompt_text: string | null
  material_filename: string | null
  material_category: string | null
  material_thumbnail_url: string | null
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

export interface ExampleFile {
  name: string
  relative_path: string
  url: string
  asset_type: 'image' | 'video' | 'audio' | 'file'
  size: number
}

export interface ExampleCategory {
  name: string
  files: ExampleFile[]
}

export interface ExampleCategoryResponse {
  categories: ExampleCategory[]
}

// ── Pipeline (Agent System) Types ──

export interface PipelineConfig {
  script: string
  image_ids: string[]
  platform: string
  duration_seconds: number
  duration_mode?: string
  no_audio?: boolean
  style: string
  voice_id: string
  transition?: string
  transition_duration?: number
  bgm_mood?: string
  bgm_volume?: number
  watermark_image_id?: string | null
}

export interface GenerateScriptResponse {
  script: string
}

export interface PipelineRun {
  id: string
  project_id: string
  trace_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  current_agent: string | null
  overall_score: number | null
  final_video_path: string | null
  error_message: string | null
  retry_count: number
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface AgentExecution {
  id: string
  agent_name: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
  attempt_number: number
  input_data?: Record<string, any> | null
  output_data?: Record<string, any> | null
  duration_ms: number | null
  error_message: string | null
  created_at: string
  completed_at: string | null
}

export interface PipelineUsageByAgent {
  agent_name: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  request_count: number
}

export interface PipelineUsageByModel {
  provider: string
  model_name: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  request_count: number
}

export interface PipelineUsageSummary {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  request_count: number
  by_agent: PipelineUsageByAgent[]
  by_model: PipelineUsageByModel[]
}

export interface ProjectPipelineUsageItem {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  current_agent: string | null
  total_tokens: number
  request_count: number
  created_at: string
}

export interface ProjectUsageSummary {
  project_id: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  request_count: number
  latest_pipeline_status: string | null
  latest_current_agent: string | null
  pipelines: ProjectPipelineUsageItem[]
}

export interface ProjectArtifactFile {
  name: string
  path: string
  url: string
  content: string | null
  shot_idx: number | null
  duration_ms: number | null
  kind: string | null
}

export interface PromptHistoryItem {
  shot_idx: number
  script_segment: string | null
  video_prompt: string
  duration_seconds: number | null
}

export interface ProjectHistoryRun {
  run_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  created_at: string
  completed_at: string | null
  current_agent: string | null
  total_tokens: number
  request_count: number
  input_script: string | null
  voice_params: Record<string, unknown> | null
  prompts: PromptHistoryItem[]
  audio_files: ProjectArtifactFile[]
  subtitle_files: ProjectArtifactFile[]
  generated_videos: ProjectArtifactFile[]
  final_videos: ProjectArtifactFile[]
}

export interface ProjectHistoryResponse {
  project_id: string
  runs: ProjectHistoryRun[]
}
