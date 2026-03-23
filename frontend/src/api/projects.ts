import api from './client'
import type { Project, ProjectHistoryResponse, ProjectUsageSummary } from '../types'

export const createProject = (name: string) =>
  api.post<Project>('/api/projects', { name }).then(r => r.data)

export const listProjects = () =>
  api.get<Project[]>('/api/projects').then(r => r.data)

export const getProject = (id: string) =>
  api.get<Project>(`/api/projects/${id}`).then(r => r.data)

export const getProjectUsage = (id: string) =>
  api.get<ProjectUsageSummary>(`/api/projects/${id}/usage`).then(r => r.data)

export const getProjectHistory = (id: string) =>
  api.get<ProjectHistoryResponse>(`/api/projects/${id}/history`).then(r => r.data)

export const updateProject = (id: string, data: { name?: string; current_step?: number }) =>
  api.patch<Project>(`/api/projects/${id}`, data).then(r => r.data)

export const deleteProject = (id: string) =>
  api.delete(`/api/projects/${id}`)
