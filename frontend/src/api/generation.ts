import api from './client'
import type { GeneratedVideo } from '../types'

export const startGeneration = (projectId: string) =>
  api.post<GeneratedVideo[]>(`/api/projects/${projectId}/generate`).then(r => r.data)

export const generateSingle = (projectId: string, promptId: string) =>
  api.post<GeneratedVideo>(`/api/projects/${projectId}/generate-single/${promptId}`).then(r => r.data)

export const getGenerations = (projectId: string) =>
  api.get<GeneratedVideo[]>(`/api/projects/${projectId}/generations`).then(r => r.data)

export const selectVideo = (projectId: string, genId: string) =>
  api.post(`/api/projects/${projectId}/generations/${genId}/select`)

export const deselectVideo = (projectId: string, genId: string) =>
  api.post(`/api/projects/${projectId}/generations/${genId}/deselect`)

export const getSelectedVideos = (projectId: string) =>
  api.get<GeneratedVideo[]>(`/api/projects/${projectId}/selected-videos`).then(r => r.data)

export const getGeneratedVideoUrl = (genId: string) =>
  `/api/generations/${genId}/video`
