import type { MaterialItem, MaterialSelection, VideoUpload } from './assets'
import type { PublishDraft, SocialAccount } from './social'

export interface PipelineConfig {
  script: string
  image_ids: string[]
  session_id?: string | null
  reference_video_id?: string | null
  reference_video_ids?: string[]
  remix_config?: RemixConfig | null
  background_template_id?: string | null
  platform: string
  duration_seconds: number
  duration_mode?: string
  no_audio?: boolean
  video_model_no_audio?: boolean
  voiceover_no_audio?: boolean
  generation_model?: string
  style: string
  voice_id: string
  transition?: string
  transition_duration?: number
  bgm_mood?: string
  bgm_volume?: number
  watermark_image_id?: string | null
}

export interface RemixConfig {
  target_duration_seconds?: number | null
  mood?: string | null
  bgm_material_id?: string | null
  bgm_mood?: string
  bgm_volume?: number
  include_source_audio?: boolean
  add_voiceover?: boolean
  voiceover_script?: string | null
}

export interface RemixPlanSegment {
  segment_idx: number
  source_video_id: string
  source_shot_idx?: number
  start_seconds: number
  end_seconds: number
  description?: string
  script_segment?: string
  narration?: string
  voiceover?: string
  role?: string
  quality_score?: number
  transition_to_next?: string
  transition_duration?: number
  reference_keyframe_path?: string
  removed?: boolean
}

export interface RemixPlan {
  title?: string
  concept?: string
  target_duration_seconds?: number
  source_videos?: Array<Record<string, unknown>>
  segments: RemixPlanSegment[]
  audio_design?: Record<string, unknown>
  analysis_report?: string
}

export interface RemixSegmentEdit {
  segment_idx: number
  source_video_id?: string
  start_seconds?: number
  end_seconds?: number
  transition_type?: string
  removed?: boolean
}

export interface GenerateScriptResponse {
  script: string
}

export interface PipelineRun {
  id: string
  project_id: string
  session_id?: string | null
  trace_id: string
  engine: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'waiting_confirmation' | 'waiting_prompt_review' | 'waiting_remix_confirmation'
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
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled'
  attempt_number: number
  input_data?: Record<string, unknown> | null
  output_data?: Record<string, unknown> | null
  duration_ms: number | null
  error_message: string | null
  progress_text?: string | null
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

export interface AutoChatMessageImagePayload {
  id: string
  url: string
  name: string
}

export interface AutoChatMessageVideoPayload {
  id: string
  name: string
  streamUrl: string
}

export interface AutoChatMessageFilePayload {
  id: string
  name: string
  url: string
  mimeType?: string | null
}

export interface DirectorPlanShot {
  shot_idx: number
  duration_seconds: number
  duration_range_label?: string
  generation_duration_seconds?: number
  sequence_role?: string
  sequence_reason?: string
  shot_purpose?: string
  script_segment?: string
  video_prompt?: string
  source_image_idx?: number
}

export interface DirectorPlanVoiceDesign {
  voice_id?: string
  speed?: number
  tone?: string
}

export interface DirectorPlan {
  run_id: string
  shot_prompts: DirectorPlanShot[]
  voice_design: DirectorPlanVoiceDesign
  director_summary?: string
  creative_concept?: string
  pacing_strategy?: string
  narration_script?: string
}

export interface AutoChatMessagePayload {
  mutedLines?: string[]
  images?: AutoChatMessageImagePayload[]
  files?: AutoChatMessageFilePayload[]
  video?: AutoChatMessageVideoPayload | null
  publishDraft?: PublishDraft | null
  directorPlan?: DirectorPlan | null
}

export interface AutoChatSessionState {
  draft_script: string | null
  background_template_id: string | null
  reference_video_id: string | null
  reference_video_ids: string[]
  video_platform: string
  video_no_audio: boolean
  video_model_no_audio?: boolean
  voiceover_no_audio?: boolean
  generation_model?: string
  duration_mode: string
  video_transition: string
  bgm_mood: string
  watermark_id: string | null
  current_run_id: string | null
}

export interface AutoChatSessionSummary {
  id: string
  project_id: string
  title: string
  status_preview: string
  latest_message_excerpt: string | null
  latest_message_role: string | null
  reference_video_name: string | null
  current_run_id: string | null
  current_run_status: string | null
  last_activity_at: string
  created_at: string
  updated_at: string
}

export interface AutoChatSessionMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant' | 'system'
  title?: string | null
  content: string
  payload?: AutoChatMessagePayload | null
  created_at: string
  updated_at: string
}

export interface AutoChatSessionDetail {
  session: AutoChatSessionSummary
  state: AutoChatSessionState
  messages: AutoChatSessionMessage[]
  selected_materials: MaterialSelection[]
  selected_material_items: MaterialItem[]
  reference_video: VideoUpload | null
  reference_videos: VideoUpload[]
  current_run: PipelineRun | null
  agent_executions: AgentExecution[]
  delivery_info: PipelineDeliveryInfo | null
  usage_summary: PipelineUsageSummary | null
  connected_social_accounts?: SocialAccount[]
  recommended_publish_account?: SocialAccount | null
  latest_publish_draft?: PublishDraft | null
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

export interface PipelineDeliveryInfo {
  previews: PlatformPreviewCard[]
  records: VideoDeliveryRecord[]
  connected_social_accounts?: SocialAccount[]
  recommended_publish_account?: SocialAccount | null
  latest_publish_draft?: PublishDraft | null
}

export interface PlatformPreviewCard {
  platform: 'douyin' | 'youtube'
  label: string
  aspect_ratio: string
  recommended_resolution: string
  cover_title: string
  headline: string
  caption: string
  layout_hint: string
  safe_zone_tip: string
  context_hint: string
  primary_action: string
}

export interface VideoDeliveryRecord {
  id: string
  user_id: string
  project_id: string
  pipeline_run_id: string
  action_type: 'save' | 'publish'
  platform: string
  status: 'pending' | 'draft' | 'saved' | 'submitted' | 'published' | 'failed'
  social_account_id?: string | null
  title: string | null
  description: string | null
  draft_payload?: Record<string, unknown> | null
  saved_video_path: string | null
  external_id: string | null
  external_url: string | null
  external_status?: string | null
  response_payload?: Record<string, unknown> | null
  platform_error_code?: string | null
  error_message: string | null
  submitted_at?: string | null
  published_at?: string | null
  created_at: string
  updated_at: string
}
