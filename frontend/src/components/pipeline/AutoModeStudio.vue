<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  chatWithAgent,
  createAutoSession,
  createAutoSessionPublishDraft,
  getAutoSession,
  listAutoSessions,
  selectAutoSessionMaterial,
  updateAutoSession,
} from '../../api/autoSessions'
import { listBackgroundTemplates } from '../../api/backgroundTemplates'
import {
  cancelPipeline,
  confirmPromptReview,
  confirmReplicationPlan,
  estimateCost,
  getPipelineAgents,
  getPipelineArtifacts,
  getPipelineDelivery,
  getPipelineRun,
  getPipelineUsage,
  launchPipeline,
  preflightCheck,
  retryShotIndices,
  savePipelineVideo,
  streamPipeline,
} from '../../api/pipeline'
import { importRepositoryDelivery, importRepositoryUpload } from '../../api/repository'
import { listProjects } from '../../api/projects'
import { listSocialAccounts, startDouyinConnect } from '../../api/socialAccounts'
import {
  resetPipeline,
  setAgentExecutions,
  setCurrentRun,
  setUsageSummary,
} from '../../stores/pipelineStore'
import type {
  AgentExecution,
  AutoChatMessagePayload,
  AutoChatSessionDetail,
  AutoChatSessionMessage,
  AutoChatSessionSummary,
  BackgroundTemplate,
  DirectorPlan,
  MaterialItem,
  MaterialSelection,
  PipelineDeliveryInfo,
  PipelineRun,
  PipelineUsageSummary,
  Project,
  PublishDraft,
  RepositoryAsset,
  RepositoryDelivery,
  RepositoryUpload,
  SocialAccount,
  VideoUpload,
} from '../../types'
import { toast } from '../../composables/useToast'

const props = defineProps<{
  projectId: string
}>()

const emit = defineEmits<{
  switchToManual: []
  switchProject: [project: Project]
  openRepositoryPicker: []
}>()

type UiMessage = Pick<AutoChatSessionMessage, 'id' | 'role' | 'title' | 'content' | 'created_at'> & {
  payload?: AutoChatMessagePayload | null
  mutedLines?: string[]
}

type DouyinOAuthMessage = {
  type?: string
  success?: boolean
  message?: string
}

const sessionSummaries = ref<AutoChatSessionSummary[]>([])
const activeSessionId = ref<string | null>(null)
const loadingSessions = ref(true)
const loadingSession = ref(false)
const creatingSession = ref(false)
const messages = ref<UiMessage[]>([])
const selectedMaterials = ref<MaterialSelection[]>([])
const backgroundTemplates = ref<BackgroundTemplate[]>([])
const backgroundTemplateId = ref<string | null>(null)
const projects = ref<Project[]>([])
const script = ref('')
const videoPlatform = ref('generic')
const videoNoAudio = ref(true)
const voiceoverNoAudio = ref(false)
const videoGenerationModel = ref('seedance1.5-pro')
const durationMode = ref<'auto' | 'fixed'>('fixed')
const videoTransition = ref('none')
const bgmMood = ref('none')
const skipVideoGeneration = ref(true)
const watermarkId = ref<string | null>(null)
const referenceVideoId = ref<string | null>(null)
const referenceVideoName = ref<string | null>(null)
const currentRun = ref<PipelineRun | null>(null)
const agentExecutions = ref<AgentExecution[]>([])
const pipelineArtifacts = ref<RepositoryAsset[]>([])
const usageSummary = ref<PipelineUsageSummary | null>(null)
const deliveryInfo = ref<PipelineDeliveryInfo | null>(null)
const connectedSocialAccounts = ref<SocialAccount[]>([])
const latestPublishDraft = ref<PublishDraft | null>(null)
const input = ref('')
const streaming = ref(false)
const streamText = ref('')
const statusLines = ref<string[]>([])
const launching = ref(false)
const abortingChat = ref(false)
const confirmingPlan = ref(false)
const confirmingPromptReview = ref(false)
const cancellingRun = ref(false)
const shotEdits = ref<Record<number, { script_segment?: string; video_prompt?: string }>>({})
const costEstimate = ref<{ estimated_total_cny: number; breakdown: Record<string, number>; shot_count: number; warning?: string | null } | null>(null)
const loadingCostEstimate = ref(false)
const retryingShots = ref<Set<number>>(new Set())
const adjustmentText = ref('')
const savingDelivery = ref(false)
const draftingPublish = ref(false)
const connectingDouyin = ref(false)
const chatEnd = ref<HTMLElement | null>(null)
let pollTimer: number | undefined
let currentStream: { close: () => void } | null = null
let pipelineStream: EventSource | null = null
let activePipelineRunId: string | null = null
let chatAbortController: AbortController | null = null
let activeChatRequestId = 0
let chatRequestSeq = 0
// Tracks run IDs for which we already fetched session messages on waiting_prompt_review,
// preventing the openSession → hydrate → startPolling → SSE event → openSession loop.
const directorPlanRefreshedRunIds = new Set<string>()

const activeSessionSummary = computed(() => sessionSummaries.value.find((item) => item.id === activeSessionId.value) || null)
const selectedImageIds = computed(() => selectedMaterials.value.map((item) => item.material_id).filter(Boolean))
const currentRunTerminal = computed(() => {
  const status = currentRun.value?.status
  return isTerminalRunStatus(status)
})
const hasActiveRunControl = computed(() => {
  return isActiveRunStatus(currentRun.value?.status)
})
const currentDirectorPlanShots = computed(() => {
  if (!currentRun.value || currentRun.value.status !== 'waiting_prompt_review') return []
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const msg = messages.value[i]
    if (msg.payload?.directorPlan?.run_id === currentRun.value.id) return msg.payload.directorPlan.shot_prompts || []
  }
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const msg = messages.value[i]
    if (msg.payload?.directorPlan) return msg.payload.directorPlan.shot_prompts || []
  }
  return []
})

const artifactGroups = computed(() => {
  const order = ['prompt_engineer', 'audio_subtitle', 'video_generator']
  return order
    .map((agent) => ({
      agent,
      items: pipelineArtifacts.value.filter((item) => item.source_agent === agent),
    }))
    .filter((group) => group.items.length > 0)
})

function isTerminalRunStatus(status: string | undefined | null) {
  return status === 'completed' || status === 'failed' || status === 'cancelled'
}

function isActiveRunStatus(status: string | undefined | null) {
  return status === 'pending' || status === 'running' || status === 'waiting_confirmation' || status === 'waiting_prompt_review'
}

function readableError(error: unknown, fallback: string) {
  const candidate = error as { userMessage?: string; message?: string }
  return candidate?.userMessage || candidate?.message || fallback
}

function isDouyinOAuthMessage(data: unknown): data is DouyinOAuthMessage {
  return Boolean(data && typeof data === 'object' && (data as DouyinOAuthMessage).type === 'vidgen-douyin-oauth')
}

function isCurrentChatRequest(requestId: number) {
  return activeChatRequestId === requestId
}

function clearActiveChatRequest(requestId?: number) {
  if (requestId !== undefined && !isCurrentChatRequest(requestId)) return
  activeChatRequestId = 0
  chatAbortController = null
  currentStream = null
}

function stopPipelineWatch() {
  window.clearInterval(pollTimer)
  pipelineStream?.close()
  pipelineStream = null
  activePipelineRunId = null
}

function statusText(status: string | undefined | null) {
  if (!status) return '未开始'
  const labels: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    waiting_confirmation: '等待确认',
    waiting_prompt_review: '镜头方案已生成，等待确认',
    skipped: '已跳过',
  }
  return labels[status] || status
}

function agentLabel(agent: string) {
  const labels: Record<string, string> = {
    prompt_engineer: '提示词 Agent',
    audio_subtitle: '音频 Agent',
    video_generator: '视频生成 Agent',
  }
  return labels[agent] || agent
}

function artifactTypeLabel(asset: RepositoryAsset) {
  const labels: Record<string, string> = {
    plan: '方案',
    prompt: '提示词',
    voice_params: '配音',
    status: '状态',
    audio: '音频',
    subtitle: '字幕',
    video_manifest: '视频清单',
    video: '视频',
  }
  return labels[asset.asset_type] || asset.asset_type
}

function isVideoArtifact(asset: RepositoryAsset) {
  return asset.asset_type === 'video' || asset.mime_type?.startsWith('video/')
}

function isAudioArtifact(asset: RepositoryAsset) {
  return asset.asset_type === 'audio' || asset.mime_type?.startsWith('audio/')
}

function formatDuration(ms: number | null) {
  if (!ms) return ''
  return `${(ms / 1000).toFixed(ms % 1000 === 0 ? 0 : 1)}s`
}

function compactText(text: string | null) {
  if (!text) return ''
  return text.length > 900 ? `${text.slice(0, 900)}...` : text
}

function payloadText(payload: unknown) {
  if (typeof payload === 'string') return payload
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>
    return String(record.content || record.text || record.message || record.delta || '')
  }
  return ''
}

function mapMessages(detail: AutoChatSessionDetail): UiMessage[] {
  return detail.messages.map((message) => ({
    id: message.id,
    role: message.role,
    title: message.title,
    content: message.content,
    created_at: message.created_at,
    payload: message.payload,
    mutedLines: message.payload?.mutedLines || [],
  }))
}

async function scrollToEnd() {
  await nextTick()
  chatEnd.value?.scrollIntoView({ block: 'end' })
}

function hydrate(detail: AutoChatSessionDetail) {
  activeSessionId.value = detail.session.id
  messages.value = mapMessages(detail)
  selectedMaterials.value = detail.selected_materials
  script.value = detail.state.draft_script || ''
  backgroundTemplateId.value = detail.state.background_template_id
  referenceVideoId.value = detail.state.reference_video_id
  referenceVideoName.value = detail.reference_video?.filename || detail.session.reference_video_name || null
  videoPlatform.value = detail.state.video_platform || 'generic'
  videoNoAudio.value = detail.state.video_model_no_audio ?? detail.state.video_no_audio
  voiceoverNoAudio.value = detail.state.voiceover_no_audio ?? false
  videoGenerationModel.value = detail.state.generation_model || 'seedance1.5-pro'
  durationMode.value = (detail.state.duration_mode as 'auto' | 'fixed') || 'fixed'
  videoTransition.value = detail.state.video_transition || 'none'
  bgmMood.value = detail.state.bgm_mood || 'none'
  watermarkId.value = detail.state.watermark_id
  currentRun.value = detail.current_run
  agentExecutions.value = detail.agent_executions
  pipelineArtifacts.value = []
  usageSummary.value = detail.usage_summary
  deliveryInfo.value = detail.delivery_info
  connectedSocialAccounts.value = detail.connected_social_accounts || []
  latestPublishDraft.value = detail.latest_publish_draft || detail.delivery_info?.latest_publish_draft || null
  setCurrentRun(detail.current_run)
  setAgentExecutions(detail.agent_executions)
  setUsageSummary(detail.usage_summary)
  shotEdits.value = {}
  costEstimate.value = null
  if (detail.current_run && !isTerminalRunStatus(detail.current_run.status)) {
    startPolling(detail.current_run.id)
    if (detail.current_run.status === 'waiting_prompt_review') fetchCostEstimate()
  } else {
    stopPipelineWatch()
    if (detail.current_run) refreshRun(detail.current_run.id)
  }
  scrollToEnd()
}

async function refreshSessions(preferredSessionId?: string) {
  loadingSessions.value = true
  try {
    sessionSummaries.value = await listAutoSessions(props.projectId)
    const target = preferredSessionId || activeSessionId.value || sessionSummaries.value[0]?.id
    if (target) await openSession(target)
    else await newSession()
  } catch {
    toast('error', '加载自动模式会话失败')
  } finally {
    loadingSessions.value = false
  }
}

async function openSession(sessionId: string) {
  loadingSession.value = true
  directorPlanRefreshedRunIds.clear()
  try {
    const detail = await getAutoSession(props.projectId, sessionId)
    hydrate(detail)
  } catch {
    toast('error', '打开会话失败')
  } finally {
    loadingSession.value = false
  }
}

async function newSession() {
  creatingSession.value = true
  try {
    const detail = await createAutoSession(props.projectId)
    if (!sessionSummaries.value.some((item) => item.id === detail.session.id)) {
      sessionSummaries.value.unshift(detail.session)
    }
    hydrate(detail)
  } catch {
    toast('error', '创建会话失败')
  } finally {
    creatingSession.value = false
  }
}

async function persistSessionState() {
  if (!activeSessionId.value) return
  try {
    await updateAutoSession(props.projectId, activeSessionId.value, {
      draft_script: script.value || null,
      background_template_id: backgroundTemplateId.value,
      reference_video_id: referenceVideoId.value,
      video_platform: videoPlatform.value,
      video_no_audio: videoNoAudio.value,
      video_model_no_audio: videoNoAudio.value,
      voiceover_no_audio: voiceoverNoAudio.value,
      duration_mode: durationMode.value,
      video_transition: videoTransition.value,
      bgm_mood: bgmMood.value,
      watermark_id: watermarkId.value,
    })
  } catch {
    // Saving state is opportunistic; launching and chat still use current local values.
  }
}

async function finishStreaming(appendLocalMessage = false, title?: string) {
  const content = streamText.value || statusLines.value.join('\n')
  if (appendLocalMessage && content) {
    messages.value.push({
      id: `local-assistant-${Date.now()}`,
      role: 'assistant',
      title: title || statusLines.value.at(-1) || null,
      content,
      created_at: new Date().toISOString(),
      mutedLines: [...statusLines.value],
    })
  }
  streaming.value = false
  streamText.value = ''
  statusLines.value = []
  currentStream = null
  await scrollToEnd()
}

function isPipelineRunPayload(value: unknown): value is PipelineRun {
  return Boolean(value && typeof value === 'object' && typeof (value as { id?: unknown }).id === 'string')
}

function maybeTrackToolRun(payload: unknown) {
  if (!payload || typeof payload !== 'object') return
  const event = payload as { result?: unknown }
  const result = event.result && typeof event.result === 'object'
    ? event.result as { run?: unknown; run_id?: unknown }
    : null
  if (!result) return

  if (isPipelineRunPayload(result.run)) {
    currentRun.value = result.run
    setCurrentRun(result.run)
    if (!isTerminalRunStatus(result.run.status)) startPolling(result.run.id)
  } else if (typeof result.run_id === 'string') {
    startPolling(result.run_id)
    refreshRun(result.run_id)
  }
}

async function stopChatStream() {
  if (!streaming.value && !currentStream && !chatAbortController) return
  abortingChat.value = true
  chatAbortController?.abort()
  currentStream?.close()
  clearActiveChatRequest()
  statusLines.value.push('已中止当前对话。')
  await finishStreaming(true, '已中止')
  abortingChat.value = false
}

async function sendMessage(forceTool?: string) {
  const content = input.value.trim()
  if (!content || !activeSessionId.value || streaming.value) return
  const sessionId = activeSessionId.value
  const requestId = ++chatRequestSeq
  const abortController = new AbortController()
  activeChatRequestId = requestId
  chatAbortController = abortController
  abortingChat.value = false

  input.value = ''
  streamText.value = ''
  statusLines.value = []
  streaming.value = true
  messages.value.push({
    id: `local-user-${Date.now()}`,
    role: 'user',
    content,
    created_at: new Date().toISOString(),
  })
  await scrollToEnd()

  try {
    const streamHandle = await chatWithAgent(
      props.projectId,
      sessionId,
      {
        role: 'user',
        content,
        payload: { mutedLines: [] },
        force_tool: forceTool,
        generation_model: videoGenerationModel.value,
        skip_video_generation: skipVideoGeneration.value,
        video_model_no_audio: videoNoAudio.value,
        voiceover_no_audio: voiceoverNoAudio.value,
      },
      {
        onText: (payload) => {
          if (!isCurrentChatRequest(requestId)) return
          streamText.value += payloadText(payload)
          scrollToEnd()
        },
        onStatus: (payload) => {
          if (!isCurrentChatRequest(requestId)) return
          const line = payloadText(payload)
          if (line) statusLines.value.push(line)
          scrollToEnd()
        },
        onToolCall: (payload) => {
          if (!isCurrentChatRequest(requestId)) return
          statusLines.value.push(`调用工具：${payloadText(payload) || 'tool_call'}`)
        },
        onToolResult: (payload) => {
          if (!isCurrentChatRequest(requestId)) return
          maybeTrackToolRun(payload)
          const toolName = payload && typeof payload === 'object' ? String((payload as { name?: unknown }).name || '') : ''
          statusLines.value.push(`工具完成：${toolName || payloadText(payload) || 'tool_result'}`)
        },
        onDone: async () => {
          if (!isCurrentChatRequest(requestId)) return
          clearActiveChatRequest(requestId)
          await finishStreaming(false)
          if (activeSessionId.value === sessionId) {
            await openSession(sessionId)
            await refreshSessions(sessionId)
          }
        },
        onError: async (error) => {
          if (!isCurrentChatRequest(requestId) || abortController.signal.aborted) return
          toast('error', error.message || '自动模式对话失败')
          statusLines.value.push('自动模式对话已断开。')
          clearActiveChatRequest(requestId)
          await finishStreaming(true, '对话失败')
        },
      },
      { signal: abortController.signal },
    )
    if (!isCurrentChatRequest(requestId) || abortController.signal.aborted) {
      streamHandle.close()
      return
    }
    currentStream = streamHandle
  } catch {
    if (!isCurrentChatRequest(requestId) || abortController.signal.aborted) return
    toast('error', '自动模式对话连接失败')
    streaming.value = false
    streamText.value = ''
    statusLines.value = []
    clearActiveChatRequest(requestId)
    await scrollToEnd()
  }
}

async function handleRepositoryPicked(
  items: MaterialItem[],
  upload: RepositoryUpload | null,
  delivery: RepositoryDelivery | null,
) {
  if (!activeSessionId.value) return
  try {
    for (const [index, item] of items.entries()) {
      await selectAutoSessionMaterial(props.projectId, activeSessionId.value, item.id, item.category, selectedMaterials.value.length + index)
    }

    let importedVideo: VideoUpload | null = null
    if (upload) importedVideo = await importRepositoryUpload(upload.id, props.projectId, activeSessionId.value)
    if (delivery) importedVideo = await importRepositoryDelivery(delivery.id, props.projectId, activeSessionId.value)

    if (importedVideo) {
      referenceVideoId.value = importedVideo.id
      referenceVideoName.value = importedVideo.filename
    }

    await persistSessionState()
    await openSession(activeSessionId.value)
    toast('success', '仓库内容已加入当前会话')
  } catch {
    toast('error', '导入仓库内容失败')
  }
}

async function launch() {
  if (!activeSessionId.value) return
  const finalScript = script.value.trim() || input.value.trim()
  if (!finalScript) {
    toast('warning', '请先输入脚本或创作要求')
    return
  }

  launching.value = true
  try {
    await persistSessionState()
    if (selectedImageIds.value.length > 0) {
      const check = await preflightCheck(props.projectId, {
        script: finalScript,
        image_count: selectedImageIds.value.length,
        duration_seconds: durationMode.value === 'fixed' ? 30 : 0,
        duration_mode: durationMode.value,
      })
      if (!check.ok && check.warning) toast('warning', check.warning, 6500)
    }

    const run = await launchPipeline(props.projectId, {
      script: finalScript,
      image_ids: selectedImageIds.value,
      session_id: activeSessionId.value,
      reference_video_id: referenceVideoId.value,
      background_template_id: backgroundTemplateId.value,
      platform: videoPlatform.value,
      duration_seconds: durationMode.value === 'fixed' ? 30 : 0,
      duration_mode: durationMode.value,
      no_audio: videoNoAudio.value,
      video_model_no_audio: videoNoAudio.value,
      voiceover_no_audio: voiceoverNoAudio.value,
      generation_model: videoGenerationModel.value,
      style: 'commercial',
      voice_id: 'default',
      transition: videoTransition.value,
      transition_duration: 0.3,
      bgm_mood: bgmMood.value,
      bgm_volume: 0.18,
      watermark_image_id: watermarkId.value,
    })
    currentRun.value = run
    setCurrentRun(run)
    await updateAutoSession(props.projectId, activeSessionId.value, { current_run_id: run.id, draft_script: finalScript })
    toast('success', '流水线已启动')
    startPolling(run.id)
  } catch {
    toast('error', '启动流水线失败')
  } finally {
    launching.value = false
  }
}

function startPolling(runId: string) {
  stopPipelineWatch()
  activePipelineRunId = runId
  pipelineStream = streamPipeline(props.projectId, runId)
  pipelineStream.addEventListener('update', (event) => {
    const payload = JSON.parse((event as MessageEvent).data) as { run?: PipelineRun; agents?: AgentExecution[] }
    if (payload.run) {
      currentRun.value = payload.run
      setCurrentRun(payload.run)
      // When pipeline enters waiting_prompt_review for the first time for this run,
      // fetch only the messages (not a full hydrate) to show the director plan bubble.
      // Using a Set prevents the refresh → hydrate → startPolling → SSE → refresh loop.
      if (
        payload.run.status === 'waiting_prompt_review' &&
        !directorPlanRefreshedRunIds.has(payload.run.id) &&
        activeSessionId.value
      ) {
        directorPlanRefreshedRunIds.add(payload.run.id)
        const sid = activeSessionId.value
        getAutoSession(props.projectId, sid)
          .then((detail) => {
            messages.value = mapMessages(detail)
            scrollToEnd()
            fetchCostEstimate()
          })
          .catch(() => {})
      }
    }
    if (payload.agents) {
      agentExecutions.value = payload.agents
      setAgentExecutions(payload.agents)
    }
    if (currentRunTerminal.value) {
      stopPipelineWatch()
    }
  })
  pipelineStream.addEventListener('done', () => {
    stopPipelineWatch()
    refreshRun(runId)
  })
  pipelineStream.addEventListener('error', () => {
    pipelineStream?.close()
    pipelineStream = null
  })
  pollTimer = window.setInterval(() => {
    refreshRun(runId)
  }, 5000)
  refreshRun(runId)
}

async function refreshRun(runId: string) {
  try {
    const [run, agents, usage, delivery, artifacts] = await Promise.all([
      getPipelineRun(props.projectId, runId),
      getPipelineAgents(props.projectId, runId).catch(() => []),
      getPipelineUsage(props.projectId, runId).catch(() => null),
      getPipelineDelivery(props.projectId, runId).catch(() => null),
      getPipelineArtifacts(props.projectId, runId).catch(() => []),
    ])
    currentRun.value = run
    agentExecutions.value = agents
    pipelineArtifacts.value = artifacts
    usageSummary.value = usage
    deliveryInfo.value = delivery
    latestPublishDraft.value = delivery?.latest_publish_draft || latestPublishDraft.value
    connectedSocialAccounts.value = delivery?.connected_social_accounts || connectedSocialAccounts.value
    setCurrentRun(run)
    setAgentExecutions(agents)
    setUsageSummary(usage)
    if (currentRunTerminal.value) {
      stopPipelineWatch()
    }
  } catch {
    if (activePipelineRunId === runId) {
      pipelineStream?.close()
      pipelineStream = null
    }
  }
}

async function cancelRunById(runId: string, showSuccess = true) {
  cancellingRun.value = true
  try {
    await cancelPipeline(props.projectId, runId)
    await refreshRun(runId)
    if (activeSessionId.value) await refreshSessions(activeSessionId.value)
    if (showSuccess) toast('success', '已取消当前流程')
  } catch {
    toast('error', '取消流程失败')
  } finally {
    cancellingRun.value = false
  }
}

async function cancelCurrentRun() {
  if (!currentRun.value) return
  await cancelRunById(currentRun.value.id)
}

async function confirmPlan(approved: boolean) {
  if (!currentRun.value) return
  confirmingPlan.value = true
  try {
    await confirmReplicationPlan(props.projectId, currentRun.value.id, approved, adjustmentText.value)
    toast('success', approved ? '方案已确认' : '已提交调整意见')
    adjustmentText.value = ''
    startPolling(currentRun.value.id)
  } catch {
    toast('error', '提交确认失败')
  } finally {
    confirmingPlan.value = false
  }
}

async function confirmPromptReviewAction() {
  if (!currentRun.value) return
  confirmingPromptReview.value = true
  try {
    const editedShots = Object.entries(shotEdits.value)
      .filter(([, edit]) => edit.script_segment !== undefined || edit.video_prompt !== undefined)
      .map(([idx, edit]) => ({ shot_idx: Number(idx), ...edit }))
    await confirmPromptReview(props.projectId, currentRun.value.id, editedShots.length > 0 ? editedShots : undefined)
    toast('success', '镜头方案已确认，继续生成视频')
    shotEdits.value = {}
    costEstimate.value = null
    startPolling(currentRun.value.id)
  } catch {
    toast('error', '确认失败，请重试')
  } finally {
    confirmingPromptReview.value = false
  }
}

async function fetchCostEstimate() {
  const shots = currentDirectorPlanShots.value
  if (!shots.length || !currentRun.value) return
  loadingCostEstimate.value = true
  try {
    costEstimate.value = await estimateCost(props.projectId, {
      shot_plan: shots as object[],
      model: videoGenerationModel.value,
      voiceover_no_audio: voiceoverNoAudio.value,
    })
  } catch {
    // Non-critical; don't block confirm
  } finally {
    loadingCostEstimate.value = false
  }
}

async function retryShotAction(shotIdx: number) {
  if (!currentRun.value) return
  retryingShots.value = new Set([...retryingShots.value, shotIdx])
  try {
    await retryShotIndices(props.projectId, currentRun.value.id, [shotIdx])
    toast('success', `镜头 ${shotIdx + 1} 已重新排队`)
    startPolling(currentRun.value.id)
  } catch {
    toast('error', `镜头 ${shotIdx + 1} 重试失败`)
  } finally {
    retryingShots.value = new Set([...retryingShots.value].filter((i) => i !== shotIdx))
  }
}

function updateShotEdit(shotIdx: number, field: 'script_segment' | 'video_prompt', value: string) {
  if (!shotEdits.value[shotIdx]) shotEdits.value[shotIdx] = {}
  shotEdits.value[shotIdx][field] = value
}

async function saveDelivery() {
  if (!currentRun.value) return
  savingDelivery.value = true
  try {
    await savePipelineVideo(props.projectId, currentRun.value.id, { title: activeSessionSummary.value?.title })
    toast('success', '成片已保存到仓库')
    await refreshRun(currentRun.value.id)
  } catch {
    toast('error', '保存成片失败')
  } finally {
    savingDelivery.value = false
  }
}

async function draftPublish() {
  if (!activeSessionId.value) return
  draftingPublish.value = true
  try {
    latestPublishDraft.value = await createAutoSessionPublishDraft(props.projectId, activeSessionId.value, {
      platform: 'douyin',
    })
    toast('success', '发布草稿已生成')
  } catch {
    toast('error', '生成发布草稿失败')
  } finally {
    draftingPublish.value = false
  }
}

async function connectDouyin() {
  connectingDouyin.value = true
  try {
    const result = await startDouyinConnect()
    window.open(result.authorization_url, '_blank')
  } catch (error) {
    toast('error', readableError(error, '无法发起抖音授权'))
  } finally {
    connectingDouyin.value = false
  }
}

async function handleDouyinOAuthMessage(event: MessageEvent) {
  if (!isDouyinOAuthMessage(event.data)) return
  const message = event.data.message || (event.data.success ? '抖音账号已连接' : '抖音授权失败')
  if (event.data.success) {
    connectedSocialAccounts.value = await listSocialAccounts().catch(() => connectedSocialAccounts.value)
    toast('success', message)
  } else {
    toast('error', message)
  }
}

async function loadStaticData() {
  const [templateList, projectList, accounts] = await Promise.all([
    listBackgroundTemplates().catch(() => []),
    listProjects().catch(() => []),
    listSocialAccounts().catch(() => []),
  ])
  backgroundTemplates.value = templateList
  projects.value = projectList
  connectedSocialAccounts.value = accounts
}

watch(
  [script, backgroundTemplateId, videoPlatform, videoNoAudio, voiceoverNoAudio, durationMode, videoTransition, bgmMood],
  () => {
    persistSessionState()
  },
  { deep: false },
)

watch(() => props.projectId, async () => {
  resetPipeline()
  pipelineArtifacts.value = []
  chatAbortController?.abort()
  currentStream?.close()
  clearActiveChatRequest()
  stopPipelineWatch()
  await loadStaticData()
  await refreshSessions()
})

onMounted(async () => {
  window.addEventListener('message', handleDouyinOAuthMessage)
  await loadStaticData()
  await refreshSessions()
})

onUnmounted(() => {
  window.removeEventListener('message', handleDouyinOAuthMessage)
  chatAbortController?.abort()
  currentStream?.close()
  clearActiveChatRequest()
  stopPipelineWatch()
})

defineExpose({ handleRepositoryPicked })
</script>

<template>
  <section class="grid h-full grid-cols-[260px_minmax(0,1fr)_340px] overflow-hidden">
    <aside class="min-h-0 overflow-auto border-r border-[#d8c9ad] bg-[#fff8ec] p-4">
      <div class="mb-4 flex items-center justify-between">
        <h2 class="font-semibold">自动会话</h2>
        <button type="button" class="rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8] disabled:opacity-50" :disabled="creatingSession" @click="newSession">
          新建
        </button>
      </div>

      <div v-if="loadingSessions" class="text-sm text-[#867351]">加载中...</div>
      <div class="space-y-2">
        <button
          v-for="session in sessionSummaries"
          :key="session.id"
          type="button"
          class="w-full rounded-lg p-3 text-left text-sm"
          :class="activeSessionId === session.id ? 'bg-[#7e9d53] text-white' : 'bg-white/75 text-[#6d5936] hover:bg-white'"
          @click="openSession(session.id)"
        >
          <div class="truncate font-medium">{{ session.title || '新会话' }}</div>
          <div class="mt-1 line-clamp-2 text-xs opacity-75">{{ session.status_preview || session.latest_message_excerpt || '准备开始' }}</div>
        </button>
      </div>

      <div class="mt-6">
        <div class="mb-2 text-xs font-medium text-[#867351]">切换项目</div>
        <select class="w-full rounded-lg border border-[#d7c7a8] bg-white px-3 py-2 text-sm" @change="emit('switchProject', projects.find((project) => project.id === ($event.target as HTMLSelectElement).value)!)">
          <option v-for="project in projects" :key="project.id" :value="project.id" :selected="project.id === projectId">
            {{ project.name }}
          </option>
        </select>
      </div>

      <button type="button" class="mt-4 w-full rounded-lg border border-[#d7c7a8] bg-white/75 px-3 py-2 text-sm hover:bg-white" @click="emit('switchToManual')">
        转到手动模式
      </button>
    </aside>

    <main class="flex min-h-0 flex-col">
      <div class="border-b border-[#d8c9ad] bg-[#fff8ec] px-6 py-4">
        <div class="flex items-center justify-between gap-4">
          <div>
            <h2 class="text-lg font-semibold">一键生成</h2>
            <p class="text-sm text-[#867351]">描述目标、选择素材或参考视频，然后交给 agent 流水线推进。</p>
          </div>
          <button type="button" class="rounded-lg bg-[#7e9d53] px-4 py-2 text-sm text-white hover:bg-[#718f47] disabled:opacity-50" :disabled="launching || !activeSessionId" @click="launch">
            {{ launching ? '启动中...' : '启动流水线' }}
          </button>
        </div>

        <div class="mt-4 grid gap-3 md:grid-cols-3">
          <label class="block">
            <span class="mb-1 block text-xs text-[#867351]">背景模板</span>
            <select v-model="backgroundTemplateId" class="w-full rounded-lg border border-[#d7c7a8] bg-white px-3 py-2 text-sm">
              <option :value="null">不使用模板</option>
              <option v-for="template in backgroundTemplates" :key="template.id" :value="template.id">{{ template.name }}</option>
            </select>
          </label>
          <label class="block">
            <span class="mb-1 block text-xs text-[#867351]">平台</span>
            <select v-model="videoPlatform" class="w-full rounded-lg border border-[#d7c7a8] bg-white px-3 py-2 text-sm">
              <option value="generic">通用</option>
              <option value="douyin">抖音</option>
              <option value="youtube">YouTube</option>
            </select>
          </label>
          <label class="block">
            <span class="mb-1 block text-xs text-[#867351]">视频模型</span>
            <select v-model="videoGenerationModel" class="w-full rounded-lg border border-[#d7c7a8] bg-white px-3 py-2 text-sm">
              <option value="seedance1.5-pro">Seedance 1.5 Pro</option>
              <option value="seedance2.0">Seedance 2.0</option>
              <option value="kling">Kling v3</option>
              <option value="mock">Mock</option>
            </select>
          </label>
        </div>

        <div class="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <button type="button" class="rounded-lg border border-[#d7c7a8] bg-white px-3 py-1.5 hover:bg-[#f4ead8]" @click="emit('openRepositoryPicker')">
            从仓库添加素材 / 参考视频
          </button>
          <button type="button" class="rounded-lg border border-[#d7c7a8] bg-white px-3 py-1.5 hover:bg-[#f4ead8]" @click="durationMode = durationMode === 'auto' ? 'fixed' : 'auto'">
            时长：{{ durationMode === 'auto' ? '自动' : '固定' }}
          </button>
          <button type="button" class="rounded-lg border border-[#d7c7a8] bg-white px-3 py-1.5 hover:bg-[#f4ead8]" @click="videoNoAudio = !videoNoAudio">
            模型原声：{{ videoNoAudio ? '关闭' : '开启' }}
          </button>
          <button type="button" class="rounded-lg border border-[#d7c7a8] bg-white px-3 py-1.5 hover:bg-[#f4ead8]" @click="voiceoverNoAudio = !voiceoverNoAudio">
            系统配音：{{ voiceoverNoAudio ? '关闭' : '开启' }}
          </button>
          <select v-model="videoTransition" class="rounded-lg border border-[#d7c7a8] bg-white px-3 py-1.5">
            <option value="none">无转场</option>
            <option value="fade">淡入淡出</option>
            <option value="crossfade">交叉淡化</option>
          </select>
          <select v-model="bgmMood" class="rounded-lg border border-[#d7c7a8] bg-white px-3 py-1.5">
            <option value="none">无 BGM</option>
            <option value="bright">明亮</option>
            <option value="calm">平稳</option>
            <option value="energetic">活力</option>
          </select>
          <button
            type="button"
            :class="['rounded-lg border px-3 py-1.5', skipVideoGeneration ? 'border-[#c7a868] bg-[#f4ead8] text-[#8b6914]' : 'border-[#d7c7a8] bg-white']"
            @click="skipVideoGeneration = !skipVideoGeneration"
          >
            视频生成：{{ skipVideoGeneration ? '关闭' : '开启' }}
          </button>
        </div>

        <div class="mt-3 flex flex-wrap gap-2">
          <span v-for="item in selectedMaterials" :key="item.id" class="rounded-full bg-[#edf5de] px-3 py-1 text-xs text-[#526b32]">
            {{ item.material?.filename || item.category }}
          </span>
          <span v-if="referenceVideoName" class="rounded-full bg-[#eef7fb] px-3 py-1 text-xs text-[#315065]">
            参考视频：{{ referenceVideoName }}
          </span>
        </div>
      </div>

      <div class="min-h-0 flex-1 space-y-4 overflow-auto p-6">
        <div v-if="loadingSession" class="text-sm text-[#867351]">打开会话中...</div>
        <article
          v-for="message in messages"
          :key="message.id"
          class="max-w-3xl rounded-lg border p-4 text-sm leading-6"
          :class="message.role === 'user' ? 'ml-auto border-[#c7d9a3] bg-[#f4f8e9]' : 'border-[#d7c7a8] bg-white/85'"
        >
          <div class="mb-1 text-xs font-medium text-[#867351]">{{ message.role === 'user' ? '你' : (message.title || 'assistant') }}</div>
          <p class="whitespace-pre-wrap">{{ message.content }}</p>
          <div v-if="message.mutedLines?.length" class="mt-3 space-y-1 text-xs text-[#8a7857]">
            <div v-for="line in message.mutedLines" :key="line">{{ line }}</div>
          </div>
          <div v-if="message.payload?.directorPlan" class="mt-3">
            <div class="mb-2 text-xs font-semibold text-[#526b32]">镜头设计方案（{{ message.payload.directorPlan.shot_prompts.length }} 镜头）</div>
            <div class="overflow-x-auto rounded-lg border border-[#d0e0b8]">
              <table class="w-full text-xs">
                <thead class="bg-[#edf5de] text-[#526b32]">
                  <tr>
                    <th class="px-3 py-2 text-left font-medium">镜头</th>
                    <th class="px-3 py-2 text-left font-medium">节奏区间</th>
                    <th class="px-3 py-2 text-left font-medium">旁白</th>
                    <th class="px-3 py-2 text-left font-medium">视频提示词</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="shot in message.payload.directorPlan.shot_prompts" :key="shot.shot_idx" class="border-t border-[#e8f0d8]">
                    <td class="px-3 py-2 text-center">{{ shot.shot_idx + 1 }}</td>
                    <td class="px-3 py-2 whitespace-nowrap">
                      {{ shot.duration_range_label || `${shot.duration_seconds}s` }}
                      <span v-if="shot.duration_range_label" class="text-[#a09070]">（{{ shot.duration_seconds }}s）</span>
                    </td>
                    <td class="px-3 py-2 leading-5 text-[#6d5936]">{{ shot.script_segment || '—' }}</td>
                    <td class="px-3 py-2 leading-5 text-[#8a7857]">{{ shot.video_prompt || '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="message.payload.directorPlan.voice_design?.voice_id" class="mt-2 flex gap-4 text-xs text-[#8a7857]">
              <span>声音：{{ message.payload.directorPlan.voice_design.voice_id }}</span>
              <span v-if="message.payload.directorPlan.voice_design.speed">语速：{{ message.payload.directorPlan.voice_design.speed }}</span>
              <span v-if="message.payload.directorPlan.voice_design.tone">情绪：{{ message.payload.directorPlan.voice_design.tone }}</span>
            </div>
            <button
              v-if="currentRun?.status === 'waiting_prompt_review' && currentRun.id === message.payload.directorPlan.run_id"
              type="button"
              class="mt-3 rounded-lg bg-[#7e9d53] px-4 py-2 text-xs text-white hover:bg-[#718f47] disabled:opacity-50"
              :disabled="confirmingPromptReview"
              @click="confirmPromptReviewAction"
            >
              {{ confirmingPromptReview ? '确认中...' : '确认并生成视频' }}
            </button>
          </div>
          <div v-if="message.payload?.images?.length" class="mt-3 grid grid-cols-3 gap-2">
            <img v-for="image in message.payload.images" :key="image.id" :src="image.url" :alt="image.name" class="h-24 rounded-lg object-cover">
          </div>
          <video v-if="message.payload?.video" class="mt-3 max-h-80 w-full rounded-lg bg-black" controls :src="message.payload.video.streamUrl" />
        </article>

        <article v-if="streaming" class="max-w-3xl rounded-lg border border-[#d7c7a8] bg-white/85 p-4 text-sm leading-6">
          <div class="mb-1 text-xs font-medium text-[#867351]">assistant</div>
          <p class="whitespace-pre-wrap">{{ streamText || '正在处理...' }}</p>
          <div class="mt-3 space-y-1 text-xs text-[#8a7857]">
            <div v-for="line in statusLines" :key="line">{{ line }}</div>
          </div>
        </article>
        <div ref="chatEnd" />
      </div>

      <form class="border-t border-[#d8c9ad] bg-[#fff9ef] p-4" @submit.prevent="sendMessage()">
        <textarea
          v-model="script"
          class="mb-2 min-h-20 w-full resize-y rounded-lg border border-[#daccb3] bg-white px-4 py-3 text-sm outline-none focus:border-[#8ca65c]"
          placeholder="最终脚本或创作要求，可直接用于启动流水线..."
        />
        <div class="flex gap-2">
          <input
            v-model="input"
            class="flex-1 rounded-lg border border-[#daccb3] bg-white px-4 py-2.5 text-sm outline-none focus:border-[#8ca65c]"
            placeholder="继续和 agent 对话，或输入 /analyze /replicate /generate..."
          >
          <button
            v-if="streaming"
            type="button"
            class="rounded-lg border border-[#b95c50] px-5 py-2 text-sm text-[#9b3f34] hover:bg-[#fff1ec] disabled:opacity-50"
            :disabled="abortingChat"
            @click="stopChatStream"
          >
            {{ abortingChat ? '中止中...' : '中止对话' }}
          </button>
          <button v-else type="submit" class="rounded-lg bg-[#7e9d53] px-5 py-2 text-sm text-white hover:bg-[#718f47] disabled:opacity-50" :disabled="!input.trim()">
            发送
          </button>
        </div>
      </form>
    </main>

    <aside class="min-h-0 overflow-auto border-l border-[#d8c9ad] bg-[#fff8ec] p-4">
      <h3 class="mb-3 font-semibold">执行状态</h3>
      <article class="mb-4 rounded-lg border border-[#d7c7a8] bg-white/80 p-4">
        <div class="flex items-center justify-between">
          <span class="text-sm font-medium">{{ statusText(currentRun?.status) }}</span>
          <span class="text-xs text-[#8a7857]">{{ currentRun?.current_agent || 'agent' }}</span>
        </div>
        <button
          v-if="hasActiveRunControl"
          type="button"
          class="mt-3 w-full rounded-lg border border-[#b95c50] px-3 py-2 text-xs text-[#9b3f34] hover:bg-[#fff1ec] disabled:opacity-50"
          :disabled="cancellingRun"
          @click="cancelCurrentRun"
        >
          {{ cancellingRun ? '取消中...' : (currentRun?.status === 'waiting_confirmation' ? '终止流程' : '取消流程') }}
        </button>
        <p v-if="currentRun?.error_message" class="mt-3 rounded-lg bg-[#fff1ec] p-3 text-xs text-[#8a3a2b]">{{ currentRun.error_message }}</p>
      </article>

      <div v-if="currentRun?.status === 'waiting_confirmation'" class="mb-4 rounded-lg border border-[#d6b46b] bg-[#fff8e5] p-4">
        <div class="mb-2 text-sm font-semibold">复刻方案待确认</div>
        <textarea v-model="adjustmentText" class="mb-3 min-h-20 w-full rounded-lg border border-[#d7c7a8] p-2 text-sm" placeholder="需要调整的地方，可留空直接确认。" />
        <div class="flex gap-2">
          <button type="button" class="rounded-lg bg-[#7e9d53] px-3 py-2 text-xs text-white disabled:opacity-50" :disabled="confirmingPlan" @click="confirmPlan(true)">
            确认
          </button>
          <button type="button" class="rounded-lg border border-[#d7c7a8] px-3 py-2 text-xs hover:bg-white disabled:opacity-50" :disabled="confirmingPlan" @click="confirmPlan(false)">
            提交调整
          </button>
        </div>
      </div>

      <div v-if="currentRun?.status === 'waiting_prompt_review'" class="mb-4 rounded-lg border border-[#a8c870] bg-[#f0f8e5] p-4">
        <div class="mb-2 text-sm font-semibold">镜头方案待确认</div>

        <!-- Cost estimate -->
        <div v-if="loadingCostEstimate" class="mb-3 text-xs text-[#8a7857]">估算费用中...</div>
        <div v-else-if="costEstimate" class="mb-3 rounded-lg bg-white/60 p-2.5 text-xs">
          <div class="flex items-center justify-between">
            <span class="font-medium text-[#526b32]">预估费用</span>
            <span class="font-bold text-[#526b32]">¥ {{ costEstimate.estimated_total_cny.toFixed(2) }}</span>
          </div>
          <div class="mt-1 flex flex-wrap gap-2 text-[#8a7857]">
            <span>视频 ¥{{ (costEstimate.breakdown.video_gen || 0).toFixed(2) }}</span>
            <span v-if="!voiceoverNoAudio">配音 ¥{{ (costEstimate.breakdown.tts || 0).toFixed(2) }}</span>
            <span v-if="costEstimate.breakdown.bgm">BGM ¥{{ (costEstimate.breakdown.bgm || 0).toFixed(2) }}</span>
          </div>
          <p v-if="costEstimate.warning" class="mt-1 text-[#a06020]">{{ costEstimate.warning }}</p>
        </div>

        <!-- Editable shot table -->
        <div v-if="currentDirectorPlanShots.length" class="mb-3">
          <div class="mb-1.5 text-xs font-medium text-[#526b32]">镜头方案（可编辑）</div>
          <div class="max-h-72 space-y-2 overflow-auto">
            <div v-for="shot in currentDirectorPlanShots" :key="shot.shot_idx" class="rounded-lg bg-white/70 p-2 text-xs">
              <div class="mb-1 flex items-center gap-2">
                <span class="font-medium text-[#526b32]">镜头 {{ shot.shot_idx + 1 }}</span>
                <span class="text-[#8a7857]">{{ shot.duration_range_label || `${shot.duration_seconds}s` }}</span>
              </div>
              <div class="mb-1">
                <div class="mb-0.5 text-[#8a7857]">旁白</div>
                <textarea
                  :value="shotEdits[shot.shot_idx]?.script_segment ?? shot.script_segment ?? ''"
                  class="w-full resize-none rounded border border-[#d7c7a8] bg-white p-1.5 text-xs leading-4 outline-none focus:border-[#8ca65c]"
                  rows="2"
                  @input="(e) => updateShotEdit(shot.shot_idx, 'script_segment', (e.target as HTMLTextAreaElement).value)"
                />
              </div>
              <div>
                <div class="mb-0.5 text-[#8a7857]">视频提示词</div>
                <textarea
                  :value="shotEdits[shot.shot_idx]?.video_prompt ?? shot.video_prompt ?? ''"
                  class="w-full resize-none rounded border border-[#d7c7a8] bg-white p-1.5 text-xs leading-4 outline-none focus:border-[#8ca65c]"
                  rows="2"
                  @input="(e) => updateShotEdit(shot.shot_idx, 'video_prompt', (e.target as HTMLTextAreaElement).value)"
                />
              </div>
            </div>
          </div>
        </div>
        <p v-else class="mb-3 text-xs text-[#526b32]">导演已完成镜头规划，请确认后继续生成视频。</p>

        <button
          type="button"
          class="w-full rounded-lg bg-[#7e9d53] px-3 py-2 text-xs text-white hover:bg-[#718f47] disabled:opacity-50"
          :disabled="confirmingPromptReview"
          @click="confirmPromptReviewAction"
        >
          {{ confirmingPromptReview ? '确认中...' : '确认并生成视频' }}
        </button>
      </div>

      <div class="mb-4 space-y-2">
        <article v-for="agent in agentExecutions" :key="agent.id" class="rounded-lg border border-[#d7c7a8] bg-white/80 p-3">
          <div class="flex items-center justify-between gap-2">
            <span class="truncate text-sm font-medium">{{ agent.agent_name }}</span>
            <span class="rounded-full bg-[#f2e8d6] px-2 py-0.5 text-xs">{{ statusText(agent.status) }}</span>
          </div>
          <p v-if="agent.progress_text" class="mt-2 whitespace-pre-wrap text-xs leading-5 text-[#8a7857]">{{ agent.progress_text }}</p>
          <p v-if="agent.error_message" class="mt-2 text-xs text-[#8a3a2b]">{{ agent.error_message }}</p>
        </article>
      </div>

      <section v-if="artifactGroups.length" class="mb-4">
        <div class="mb-2 text-sm font-semibold">中间产物</div>
        <div v-for="group in artifactGroups" :key="group.agent" class="mb-3">
          <div class="mb-2 text-xs font-medium text-[#867351]">{{ agentLabel(group.agent) }}</div>
          <article v-for="asset in group.items" :key="asset.id" class="mb-2 rounded-lg border border-[#d7c7a8] bg-white/80 p-3">
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <div class="truncate text-sm font-medium">{{ asset.title || asset.asset_key }}</div>
                <div class="mt-1 text-xs text-[#8a7857]">
                  {{ artifactTypeLabel(asset) }}
                  <span v-if="formatDuration(asset.duration_ms)"> · {{ formatDuration(asset.duration_ms) }}</span>
                </div>
              </div>
              <a v-if="asset.file_url" class="shrink-0 rounded-lg border border-[#d7c7a8] px-2 py-1 text-xs hover:bg-[#f4ead8]" :href="asset.file_url" target="_blank" rel="noreferrer">
                打开
              </a>
            </div>

            <video v-if="isVideoArtifact(asset) && asset.file_url" class="mt-2 max-h-56 w-full rounded-lg bg-black object-contain" controls :src="asset.file_url" />
            <audio v-else-if="isAudioArtifact(asset) && asset.file_url" class="mt-2 w-full" controls :src="asset.file_url" />
            <p v-if="asset.text_content" class="mt-2 max-h-36 overflow-auto whitespace-pre-wrap rounded-lg bg-[#fff8ec] p-2 text-xs leading-5 text-[#6d5936]">
              {{ compactText(asset.text_content) }}
            </p>
            <p v-if="asset.description && asset.asset_type !== 'prompt'" class="mt-2 text-xs leading-5 text-[#8a7857]">{{ asset.description }}</p>
            <button
              v-if="isVideoArtifact(asset) && currentRunTerminal && asset.metadata?.shot_idx !== undefined"
              type="button"
              class="mt-2 rounded-lg border border-[#d7c7a8] px-2 py-1 text-xs hover:bg-[#f4ead8] disabled:opacity-50"
              :disabled="retryingShots.has(Number(asset.metadata.shot_idx))"
              @click="retryShotAction(Number(asset.metadata.shot_idx))"
            >
              {{ retryingShots.has(Number(asset.metadata.shot_idx)) ? '重试中...' : '重新生成' }}
            </button>
          </article>
        </div>
      </section>

      <article v-if="usageSummary" class="mb-4 rounded-lg border border-[#d7c7a8] bg-white/80 p-4">
        <div class="mb-2 text-sm font-semibold">Token 消耗</div>
        <div class="grid grid-cols-2 gap-2 text-xs">
          <div class="rounded-lg bg-[#fff8ec] p-2">总计<br><b>{{ usageSummary.total_tokens }}</b></div>
          <div class="rounded-lg bg-[#fff8ec] p-2">请求<br><b>{{ usageSummary.request_count }}</b></div>
        </div>
      </article>

      <article v-if="deliveryInfo || currentRun?.final_video_path" class="rounded-lg border border-[#d7c7a8] bg-white/80 p-4">
        <div class="mb-3 text-sm font-semibold">交付</div>
        <div class="space-y-2">
          <button type="button" class="w-full rounded-lg bg-[#7e9d53] px-3 py-2 text-xs text-white disabled:opacity-50" :disabled="savingDelivery || !currentRun" @click="saveDelivery">
            {{ savingDelivery ? '保存中...' : '保存到仓库' }}
          </button>
          <button type="button" class="w-full rounded-lg border border-[#d7c7a8] px-3 py-2 text-xs hover:bg-white disabled:opacity-50" :disabled="draftingPublish || !activeSessionId" @click="draftPublish">
            {{ draftingPublish ? '生成中...' : '生成发布草稿' }}
          </button>
          <button
            v-if="connectedSocialAccounts.length === 0"
            type="button"
            class="w-full rounded-lg border border-[#d7c7a8] px-3 py-2 text-xs hover:bg-white disabled:opacity-50"
            :disabled="connectingDouyin"
            @click="connectDouyin"
          >
            {{ connectingDouyin ? '打开授权中...' : '连接抖音账号' }}
          </button>
        </div>
        <div v-if="latestPublishDraft" class="mt-3 rounded-lg bg-[#fff8ec] p-3 text-xs leading-5">
          <div class="font-medium">{{ latestPublishDraft.title }}</div>
          <p class="mt-1 whitespace-pre-wrap">{{ latestPublishDraft.description }}</p>
          <div class="mt-1 text-[#8a7857]">{{ latestPublishDraft.hashtags.join(' ') }}</div>
        </div>
      </article>
    </aside>
  </section>
</template>
