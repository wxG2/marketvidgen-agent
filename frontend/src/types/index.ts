export interface AuthUser {
  id: string
  username: string
  role: 'admin' | 'user'
  is_active: boolean
  created_at: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  password: string
}

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
  session_id?: string | null
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

export interface BackgroundTemplate {
  id: string
  user_id: string
  name: string
  brand_info: string | null
  user_requirements: string | null
  character_name: string | null
  identity: string | null
  scene_context: string | null
  tone_style: string | null
  visual_style: string | null
  do_not_include: string | null
  notes: string | null
  learned_preferences: string | null
  last_learned_summary: string | null
  learning_count: number
  updated_by: string
  compiled_background_context: string
  created_at: string
  updated_at: string
}

export interface BackgroundTemplateLearningLog {
  id: string
  template_id: string
  pipeline_run_id: string
  before_snapshot: string
  applied_patch: string
  after_snapshot: string
  summary: string | null
  created_at: string
}

export interface BackgroundTemplateKeywordDraft {
  name: string
  brand_info: string | null
  user_requirements: string | null
  character_name: string | null
  identity: string | null
  scene_context: string | null
  tone_style: string | null
  visual_style: string | null
  do_not_include: string | null
  notes: string | null
}

// ── Pipeline (Agent System) Types ──

export interface PipelineConfig {
  script: string
  image_ids: string[]
  session_id?: string | null
  reference_video_id?: string | null
  background_template_id?: string | null
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
  session_id?: string | null
  trace_id: string
  engine: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'waiting_confirmation'
  current_agent: string | null
  swarm_state_json?: string | null
  swarm_state?: Record<string, any> | null
  latest_lead_message?: string | null
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
  progress_text?: string | null
  created_at: string
  completed_at: string | null
}

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
  draft_payload?: Record<string, any> | null
  saved_video_path: string | null
  external_id: string | null
  external_url: string | null
  external_status?: string | null
  response_payload?: Record<string, any> | null
  platform_error_code?: string | null
  error_message: string | null
  submitted_at?: string | null
  published_at?: string | null
  created_at: string
  updated_at: string
}

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

export interface PipelineDeliveryInfo {
  previews: PlatformPreviewCard[]
  records: VideoDeliveryRecord[]
  connected_social_accounts?: SocialAccount[]
  recommended_publish_account?: SocialAccount | null
  latest_publish_draft?: PublishDraft | null
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

export interface AutoChatMessagePayload {
  mutedLines?: string[]
  images?: AutoChatMessageImagePayload[]
  files?: AutoChatMessageFilePayload[]
  video?: AutoChatMessageVideoPayload | null
  publishDraft?: PublishDraft | null
}

export interface AutoChatSessionState {
  draft_script: string | null
  background_template_id: string | null
  reference_video_id: string | null
  video_platform: string
  video_no_audio: boolean
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

// ── Chat (ReAct Agent) Types ──

export type ChatEventType = 'reasoning' | 'tool_call' | 'tool_result' | 'tool_progress' | 'error' | 'done'

export interface ChatStreamEvent {
  event: ChatEventType
  data: Record<string, any>
}

export interface ToolCallInfo {
  tool_name: string
  input: Record<string, any>
  call_id: string
  result?: string
  media_urls?: string[]
  status: 'running' | 'completed' | 'failed'
}

export interface ChatMessageItem {
  id: string
  role: 'user' | 'assistant'
  content: string
  tool_calls?: ToolCallInfo[]
  timestamp: number
}
