import api from './client'
import type { PipelineConfig, PipelineRun, AgentExecution, PipelineUsageSummary, GenerateScriptResponse } from '../types'

export const launchPipeline = (projectId: string, config: PipelineConfig) =>
  api.post<PipelineRun>(`/api/projects/${projectId}/pipeline`, config).then(r => r.data)

export const listPipelines = (projectId: string) =>
  api.get<PipelineRun[]>(`/api/projects/${projectId}/pipelines`).then(r => r.data)

export const getPipelineRun = (projectId: string, runId: string) =>
  api.get<PipelineRun>(`/api/projects/${projectId}/pipeline/${runId}`).then(r => r.data)

export const getPipelineAgents = (projectId: string, runId: string) =>
  api.get<AgentExecution[]>(`/api/projects/${projectId}/pipeline/${runId}/agents`).then(r => r.data)

export const getPipelineUsage = (projectId: string, runId: string) =>
  api.get<PipelineUsageSummary>(`/api/projects/${projectId}/pipeline/${runId}/usage`).then(r => r.data)

export const cancelPipeline = (projectId: string, runId: string) =>
  api.post(`/api/projects/${projectId}/pipeline/${runId}/cancel`).then(r => r.data)

export const retryFailedAgent = (projectId: string, runId: string) =>
  api.post<PipelineRun>(`/api/projects/${projectId}/pipeline/${runId}/retry-agent`).then(r => r.data)

export interface PreflightCheckResult {
  ok: boolean
  warning: string | null
  estimated_audio_seconds: number
  max_video_seconds: number
  recommended_image_count: number
}

export const preflightCheck = (projectId: string, data: { script: string; image_count: number; duration_seconds: number; duration_mode: string }) =>
  api.post<PreflightCheckResult>(`/api/projects/${projectId}/preflight-check`, data).then(r => r.data)

export const generateScript = (projectId: string, imageIds: string[]) =>
  api.post<GenerateScriptResponse>(`/api/projects/${projectId}/generate-script`, { image_ids: imageIds }).then(r => r.data)
