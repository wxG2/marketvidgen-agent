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
