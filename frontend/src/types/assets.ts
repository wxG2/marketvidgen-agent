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

export interface ModelImage {
  id: string
  project_id: string
  filename: string
  file_url: string
  width: number | null
  height: number | null
  created_at: string
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
  prompt_text: string | null
  material_filename: string | null
  material_category: string | null
  material_thumbnail_url: string | null
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
