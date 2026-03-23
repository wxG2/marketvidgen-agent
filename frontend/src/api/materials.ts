import api from './client'
import type { MaterialCategory, MaterialItem, MaterialsPage, MaterialSelection } from '../types'

export const scanMaterials = () =>
  api.post('/api/materials/scan').then(r => r.data)

export const getCategories = () =>
  api.get<MaterialCategory[]>('/api/materials/categories').then(r => r.data)

export const getMaterials = (category: string, page = 1, pageSize = 50) =>
  api.get<MaterialsPage>('/api/materials', { params: { category, page, page_size: pageSize } }).then(r => r.data)

export const selectMaterial = (projectId: string, materialId: string, category: string, sortOrder = 0) =>
  api.post<MaterialSelection>(`/api/projects/${projectId}/materials/select`, {
    material_id: materialId, category, sort_order: sortOrder,
  }).then(r => r.data)

export const deselectMaterial = (projectId: string, materialId: string) =>
  api.delete(`/api/projects/${projectId}/materials/select/${materialId}`)

export const getSelectedMaterials = (projectId: string) =>
  api.get<MaterialSelection[]>(`/api/projects/${projectId}/materials/selected`).then(r => r.data)

export const deleteMaterial = (materialId: string) =>
  api.delete(`/api/materials/${materialId}`).then(r => r.data)

export const deleteCategory = (category: string) =>
  api.delete(`/api/materials/categories/${encodeURIComponent(category)}`).then(r => r.data)

export interface UploadStats {
  files: number
  categories: number
  skipped: number
  uploaded_items?: MaterialItem[]
  selected_items?: MaterialSelection[]
}

export const uploadMaterialFolder = (
  files: { file: File; relativePath: string }[],
  onProgress?: (pct: number) => void,
) => {
  const fd = new FormData()
  const paths: string[] = []
  for (const { file, relativePath } of files) {
    fd.append('files', file)
    paths.push(relativePath)
  }
  fd.append('paths', JSON.stringify(paths))
  return api.post<UploadStats>('/api/materials/upload', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    },
  }).then(r => r.data)
}

export const uploadProjectMaterials = (
  projectId: string,
  files: { file: File; relativePath: string }[],
  autoSelect = true,
  onProgress?: (pct: number) => void,
) => {
  const fd = new FormData()
  const paths: string[] = []
  for (const { file, relativePath } of files) {
    fd.append('files', file)
    paths.push(relativePath)
  }
  fd.append('paths', JSON.stringify(paths))
  fd.append('auto_select', String(autoSelect))
  return api.post<UploadStats>(`/api/projects/${projectId}/materials/upload`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    },
  }).then(r => r.data)
}
