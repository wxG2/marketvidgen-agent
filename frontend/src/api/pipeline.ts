import api from './client'
import type {
  PipelineConfig,
  PipelineRun,
  AgentExecution,
  PipelineUsageSummary,
  GenerateScriptResponse,
  PipelineDeliveryInfo,
  VideoDeliveryRecord,
  RepositoryAsset,
  RemixSegmentEdit,
} from '../types'

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

export const getPipelineDelivery = (projectId: string, runId: string) =>
  api.get<PipelineDeliveryInfo>(`/api/projects/${projectId}/pipeline/${runId}/delivery`).then(r => r.data)

export const getPipelineFinalVideoUrl = (projectId: string, runId: string) =>
  `/api/projects/${projectId}/pipeline/${runId}/final-video`

export const getPipelineArtifacts = (projectId: string, runId: string) =>
  api.get<RepositoryAsset[]>(`/api/projects/${projectId}/pipeline/${runId}/artifacts`).then(r => r.data)

export const savePipelineVideo = (
  projectId: string,
  runId: string,
  payload?: { title?: string; description?: string },
) =>
  api.post<VideoDeliveryRecord>(`/api/projects/${projectId}/pipeline/${runId}/delivery/save`, payload || {}).then(r => r.data)

export const publishPipelineVideoToDouyin = (
  projectId: string,
  runId: string,
  payload: {
    social_account_id: string
    title: string
    description: string
    hashtags?: string[]
    visibility?: string
    cover_title?: string | null
  },
) =>
  api.post<VideoDeliveryRecord>(`/api/projects/${projectId}/pipeline/${runId}/delivery/publish-douyin`, payload).then(r => r.data)

export const cancelPipeline = (projectId: string, runId: string) =>
  api.post(`/api/projects/${projectId}/pipeline/${runId}/cancel`).then(r => r.data)

export const retryFailedAgent = (projectId: string, runId: string) =>
  api.post<PipelineRun>(`/api/projects/${projectId}/pipeline/${runId}/retry-agent`).then(r => r.data)

export const confirmReplicationPlan = (
  projectId: string,
  runId: string,
  approved: boolean,
  adjustments?: string,
) =>
  api.post(`/api/projects/${projectId}/pipeline/${runId}/confirm-plan`, {
    approved,
    adjustments: adjustments || null,
  }).then(r => r.data)

export const confirmRemixPlan = (
  projectId: string,
  runId: string,
  payload: {
    approved: boolean
    adjustments?: string | null
    edited_segments?: RemixSegmentEdit[]
  },
) =>
  api.post(`/api/projects/${projectId}/pipeline/${runId}/confirm-remix`, {
    approved: payload.approved,
    adjustments: payload.adjustments || null,
    edited_segments: payload.edited_segments || [],
  }).then(r => r.data)

export interface PreflightCheckResult {
  ok: boolean
  warning: string | null
  estimated_audio_seconds: number
  max_video_seconds: number
  recommended_image_count: number
  estimated_tokens: number
}

export const preflightCheck = (projectId: string, data: { script: string; image_count: number; duration_seconds: number; duration_mode: string }) =>
  api.post<PreflightCheckResult>(`/api/projects/${projectId}/preflight-check`, data).then(r => r.data)

export const generateScript = (projectId: string, imageIds: string[]) =>
  api.post<GenerateScriptResponse>(`/api/projects/${projectId}/generate-script`, { image_ids: imageIds }).then(r => r.data)

export interface ShotEdit {
  shot_idx: number
  script_segment?: string
  video_prompt?: string
  duration_seconds?: number
}

export interface CostEstimate {
  estimated_total_cny: number
  breakdown: { video_gen: number; tts: number; llm: number; bgm: number }
  shot_count: number
  total_generation_seconds: number
  warning?: string | null
}

export const confirmPromptReview = (
  projectId: string,
  runId: string,
  editedShots?: ShotEdit[],
) =>
  api.post(`/api/projects/${projectId}/pipeline/${runId}/confirm-prompt-review`, {
    edited_shots: editedShots ?? null,
  }).then(r => r.data)

export const retryShotIndices = (projectId: string, runId: string, shotIndices: number[]) =>
  api.post(`/api/projects/${projectId}/pipeline/${runId}/retry-shot`, {
    shot_indices: shotIndices,
  }).then(r => r.data)

export const estimateCost = (
  projectId: string,
  payload: {
    shot_plan: object[]
    model?: string
    voiceover_no_audio?: boolean
    tts_char_count?: number
    bgm_mood?: string
  },
) =>
  api.post<CostEstimate>(`/api/projects/${projectId}/pipeline/estimate-cost`, payload).then(r => r.data)

/**
 * Open an SSE stream for a pipeline run.
 * Returns an EventSource; caller should listen for 'update', 'done', 'error' events.
 */
export const streamPipeline = (projectId: string, runId: string): EventSource =>
  new EventSource(`/api/projects/${projectId}/pipeline/${runId}/stream`, { withCredentials: true })
