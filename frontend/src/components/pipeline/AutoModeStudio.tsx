import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ComponentType } from 'react'
import { usePipelineStore } from '../../stores/pipelineStore'
import {
  appendAutoSessionMessage,
  createAutoSessionPublishDraft,
  createAutoSession,
  getAutoSession,
  listAutoSessions,
  selectAutoSessionMaterial,
  deselectAutoSessionMaterial,
  updateAutoSession,
  updateAutoSessionMessage,
} from '../../api/autoSessions'
import { listBackgroundTemplates } from '../../api/backgroundTemplates'
import {
  cancelPipeline,
  confirmReplicationPlan,
  generateScript,
  getPipelineAgents,
  getPipelineDelivery,
  getPipelineRun,
  getPipelineUsage,
  launchPipeline,
  publishPipelineVideoToDouyin,
  preflightCheck,
  retryFailedAgent,
  savePipelineVideo,
  streamPipeline,
} from '../../api/pipeline'
import type { PreflightCheckResult } from '../../api/pipeline'
import { listProjects } from '../../api/projects'
import { listSocialAccounts, startDouyinConnect } from '../../api/socialAccounts'
import { getVideoStreamUrl } from '../../api/upload'
import type {
  AgentExecution,
  AutoChatMessagePayload,
  AutoChatSessionDetail,
  AutoChatSessionMessage,
  AutoChatSessionSummary,
  BackgroundTemplate,
  MaterialItem,
  MaterialSelection,
  PipelineDeliveryInfo,
  PublishDraft,
  Project,
  SocialAccount,
  VideoUpload,
} from '../../types'
import { cn } from '../../lib/utils'
import { useToast } from '../ui/Toast'
import CapyAvatar from '../ui/CapyAvatar'
import MaterialPickerModal from '../materials/MaterialPickerModal'
import {
  AlertTriangle, Check, ChevronDown, ChevronUp, ClipboardCopy, Download,
  FolderOpen, FolderUp, ImagePlus, Loader2, MessageSquareText, Play, Plus,
  RotateCcw, Send, Sparkles, StopCircle, Video, Volume2, Wand2, X,
} from 'lucide-react'

const AGENT_LABELS: Record<string, string> = {
  swarm_lead: 'Swarm Lead',
  orchestrator: '调度 Agent',
  prompt_engineer: '提示词设计 Agent',
  audio_subtitle: '音频字幕 Agent',
  video_generator: '视频生成 Agent',
  video_editor: '视频剪辑 Agent',
}

type ChatMessage = {
  id: string
  role: 'assistant' | 'user' | 'system'
  title?: string
  content: string
  mutedLines?: string[]
  images?: { id: string; url: string; name: string }[]
  files?: { id: string; name: string; url: string; mimeType?: string | null }[]
  video?: { id: string; name: string; streamUrl: string }
  publishDraft?: PublishDraft | null
}

interface Props {
  projectId: string
  onSwitchToManual: () => void
  onSwitchProject?: (project: Project) => void
  onRegisterOpenPicker?: (fn: () => void) => void
}

function summarizeStatus(run: { status: string; current_agent?: string | null } | null): string {
  if (!run) return '等待发送'
  if (run.status === 'completed') return '已完成'
  if (run.status === 'failed') return '失败'
  if (run.status === 'cancelled') return '已取消'
  if (run.status === 'waiting_confirmation') return '等待确认方案'
  if (run.current_agent) return `执行中：${AGENT_LABELS[run.current_agent] || run.current_agent}`
  return '生成中'
}

export default function AutoModeStudio({ projectId, onSwitchToManual, onSwitchProject, onRegisterOpenPicker }: Props) {
  const {
    currentRun,
    setCurrentRun,
    agentExecutions,
    setAgentExecutions,
    usageSummary,
    setUsageSummary,
  } = usePipelineStore()
  const { toast } = useToast()

  const [sessionSummaries, setSessionSummaries] = useState<AutoChatSessionSummary[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [loadingSessionDetail, setLoadingSessionDetail] = useState(false)
  const [creatingSession, setCreatingSession] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [selections, setSelections] = useState<MaterialSelection[]>([])
  const [backgroundTemplates, setBackgroundTemplates] = useState<BackgroundTemplate[]>([])
  const [backgroundTemplateId, setBackgroundTemplateId] = useState<string | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [showPicker, setShowPicker] = useState(false)
  const [script, setScript] = useState('')
  const [videoPlatform, setVideoPlatform] = useState('generic')
  const [videoNoAudio, setVideoNoAudio] = useState(true)
  const [durationMode, setDurationMode] = useState<'auto' | 'fixed'>('fixed')
  const [videoTransition, setVideoTransition] = useState('none')
  const [bgmMood, setBgmMood] = useState('none')
  const [watermarkId, setWatermarkId] = useState<string | null>(null)
  const [preflightWarning, setPreflightWarning] = useState<PreflightCheckResult | null>(null)
  const [launching, setLaunching] = useState(false)
  const [generatingScript, setGeneratingScript] = useState(false)
  const [referenceVideoId, setReferenceVideoId] = useState<string | null>(null)
  const [referenceVideoName, setReferenceVideoName] = useState<string | null>(null)
  const [confirmingPlan, setConfirmingPlan] = useState(false)
  const [adjustmentText, setAdjustmentText] = useState('')
  const [showAdjustInput, setShowAdjustInput] = useState(false)
  const [deliveryInfo, setDeliveryInfo] = useState<PipelineDeliveryInfo | null>(null)
  const [connectedSocialAccounts, setConnectedSocialAccounts] = useState<SocialAccount[]>([])
  const [recommendedPublishAccount, setRecommendedPublishAccount] = useState<SocialAccount | null>(null)
  const [latestPublishDraft, setLatestPublishDraft] = useState<PublishDraft | null>(null)
  const [selectedPublishAccountId, setSelectedPublishAccountId] = useState<string | null>(null)
  const [connectingDouyin, setConnectingDouyin] = useState(false)
  const [draftingPublish, setDraftingPublish] = useState(false)
  const [publishingDraftMessageId, setPublishingDraftMessageId] = useState<string | null>(null)
  const [terminatingPlan, setTerminatingPlan] = useState(false)
  const [processPanelOpenMap, setProcessPanelOpenMap] = useState<Record<string, boolean>>({})
  const sseRef = useRef<EventSource | null>(null)
  const analysisMessageIdRef = useRef<string | null>(null)
  const analysisReportMessageIdRef = useRef<string | null>(null)
  const replicationPlanMessageIdRef = useRef<string | null>(null)
  const analysisReportAppendingRef = useRef(false)
  const replicationPlanAppendingRef = useRef(false)
  const messagesRef = useRef<ChatMessage[]>(messages)
  messagesRef.current = messages
  const lastAnalysisLineRef = useRef<string | null>(null)
  const hydratingSessionRef = useRef(false)
  const sessionPatchTimerRef = useRef<number | null>(null)
  const draftingRunIdRef = useRef<string | null>(null)

  const activeSessionSummary = useMemo(
    () => sessionSummaries.find((item) => item.id === activeSessionId) || null,
    [activeSessionId, sessionSummaries],
  )

  const selectedMaterials = useMemo(
    () => selections.map((selection) => selection.material).filter((item): item is MaterialItem => Boolean(item)),
    [selections],
  )
  const selectedBackgroundTemplate = useMemo(
    () => backgroundTemplates.find((item) => item.id === backgroundTemplateId) || null,
    [backgroundTemplateId, backgroundTemplates],
  )

  const latestExecByAgent = useMemo(() => {
    const map = new Map<string, AgentExecution>()
    for (const execution of agentExecutions) {
      const existing = map.get(execution.agent_name)
      if (!existing || new Date(execution.created_at) > new Date(existing.created_at)) {
        map.set(execution.agent_name, execution)
      }
    }
    return map
  }, [agentExecutions])

  const visibleExecutions = AGENT_ORDER
    .map((agentName) => latestExecByAgent.get(agentName))
    .filter((execution): execution is AgentExecution => Boolean(execution))
  const currentExecution = visibleExecutions.find((execution) => execution.status !== 'completed') || null
  const completedExecutions = visibleExecutions.filter((execution) => execution.status === 'completed')
  const runStatusText = summarizeStatus(currentRun)
  const hasActiveRunControl = Boolean(
    currentRun && (currentRun.status === 'pending' || currentRun.status === 'running' || currentRun.status === 'waiting_confirmation'),
  )
  const hasReferenceVideo = Boolean(referenceVideoId)
  const latestAssistantMessageId = useMemo(
    () => [...messages].reverse().find((message) => message.role === 'assistant')?.id || null,
    [messages],
  )

  const replicationPlan = useMemo(() => {
    if (currentRun?.status !== 'waiting_confirmation') return null
    const orchExec = agentExecutions.find(
      (execution) => execution.agent_name === 'orchestrator' && execution.status === 'completed',
    )
    return orchExec?.output_data?.replication_plan ?? null
  }, [agentExecutions, currentRun?.status])

  const orchestratorExecution = useMemo(
    () => [...agentExecutions]
      .filter((execution) => execution.agent_name === 'orchestrator')
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0] || null,
    [agentExecutions],
  )
  const replicationOutput = useMemo(
    () => orchestratorExecution?.output_data ?? null,
    [orchestratorExecution],
  )

  const applySessionSummaryPatch = useCallback((sessionId: string, patch: Partial<AutoChatSessionSummary>) => {
    setSessionSummaries((prev) => {
      const next = prev.map((item) => (item.id === sessionId ? { ...item, ...patch } : item))
      return [...next].sort(
        (a, b) => new Date(b.last_activity_at).getTime() - new Date(a.last_activity_at).getTime(),
      )
    })
  }, [])

  const mapSessionMessage = useCallback((message: AutoChatSessionMessage): ChatMessage => ({
    id: message.id,
    role: message.role,
    title: message.title || undefined,
    content: message.content,
    mutedLines: message.payload?.mutedLines || undefined,
    images: message.payload?.images || undefined,
    files: message.payload?.files || undefined,
    video: message.payload?.video || undefined,
    publishDraft: message.payload?.publishDraft || undefined,
  }), [])

  const hydrateFromSessionDetail = useCallback((detail: AutoChatSessionDetail) => {
    hydratingSessionRef.current = true
    setMessages(detail.messages.map(mapSessionMessage))
    setSelections(detail.selected_materials)
    setSelectedIds(new Set(detail.selected_materials.map((item) => item.material_id)))
    setBackgroundTemplateId(detail.state.background_template_id)
    setScript(detail.state.draft_script || '')
    setVideoPlatform(detail.state.video_platform || 'generic')
    setVideoNoAudio(detail.state.video_no_audio)
    setDurationMode(detail.state.duration_mode === 'auto' ? 'auto' : 'fixed')
    setVideoTransition(detail.state.video_transition || 'none')
    setBgmMood(detail.state.bgm_mood || 'none')
    setWatermarkId(detail.state.watermark_id)
    setReferenceVideoId(detail.reference_video?.id || detail.state.reference_video_id || null)
    setReferenceVideoName(detail.reference_video?.filename || null)
    setCurrentRun(detail.current_run)
    setAgentExecutions(detail.agent_executions)
    setUsageSummary(detail.usage_summary)
    setDeliveryInfo(detail.delivery_info)
    setConnectedSocialAccounts(detail.connected_social_accounts || detail.delivery_info?.connected_social_accounts || [])
    setRecommendedPublishAccount(detail.recommended_publish_account || detail.delivery_info?.recommended_publish_account || null)
    setLatestPublishDraft(detail.latest_publish_draft || detail.delivery_info?.latest_publish_draft || null)
    setSelectedPublishAccountId(
      detail.latest_publish_draft?.social_account_id
      || detail.recommended_publish_account?.id
      || detail.connected_social_accounts?.[0]?.id
      || null,
    )
    setPreflightWarning(null)
    setAdjustmentText('')
    setShowAdjustInput(false)
    const latestAnalysisMessage = [...detail.messages].reverse().find((item) => item.title === '复刻解析进行中')
    const latestAnalysisReportMessage = [...detail.messages].reverse().find((item) => item.title === '上传视频解析报告')
    analysisMessageIdRef.current = latestAnalysisMessage?.id || null
    analysisReportMessageIdRef.current = latestAnalysisReportMessage?.id || null
    analysisReportAppendingRef.current = false
    replicationPlanAppendingRef.current = false
    lastAnalysisLineRef.current = latestAnalysisMessage?.payload?.mutedLines?.slice(-1)[0] || null
    applySessionSummaryPatch(detail.session.id, detail.session)
    window.setTimeout(() => {
      hydratingSessionRef.current = false
    }, 0)
  }, [applySessionSummaryPatch, mapSessionMessage, setAgentExecutions, setCurrentRun, setUsageSummary])

  const openSession = useCallback(async (sessionId: string) => {
    setActiveSessionId(sessionId)
    setLoadingSessionDetail(true)
    try {
      const detail = await getAutoSession(projectId, sessionId)
      hydrateFromSessionDetail(detail)
    } catch (error) {
      const msg = error instanceof Error ? error.message : '未知错误'
      toast('error', `加载会话失败：${msg}`)
    } finally {
      setLoadingSessionDetail(false)
    }
  }, [hydrateFromSessionDetail, projectId, toast])

  const refreshSessionList = useCallback(async (preferredSessionId?: string) => {
    setLoadingSessions(true)
    try {
      const items = await listAutoSessions(projectId)
      setSessionSummaries(items)
      const nextId = preferredSessionId && items.some((item) => item.id === preferredSessionId)
        ? preferredSessionId
        : items[0]?.id || null
      if (nextId) {
        await openSession(nextId)
      } else {
        setActiveSessionId(null)
        setMessages([])
        setSelections([])
        setSelectedIds(new Set())
        setReferenceVideoId(null)
        setReferenceVideoName(null)
        setCurrentRun(null)
        setAgentExecutions([])
        setUsageSummary(null)
        setDeliveryInfo(null)
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : '未知错误'
      toast('error', `加载会话列表失败：${msg}`)
    } finally {
      setLoadingSessions(false)
    }
  }, [openSession, projectId, setAgentExecutions, setCurrentRun, setUsageSummary, toast])

  const appendPersistedMessage = useCallback(async (message: Omit<ChatMessage, 'id'>) => {
    if (!activeSessionId) return null
    const payload: AutoChatMessagePayload | undefined =
      message.mutedLines?.length || message.images?.length || message.files?.length || message.video || message.publishDraft
        ? {
            mutedLines: message.mutedLines,
            images: message.images,
            files: message.files,
            video: message.video || null,
            publishDraft: message.publishDraft || null,
          }
        : undefined
    const created = await appendAutoSessionMessage(projectId, activeSessionId, {
      role: message.role,
      title: message.title,
      content: message.content,
      payload,
    })
    const mapped = mapSessionMessage(created)
    setMessages((prev) => [...prev, mapped])
    applySessionSummaryPatch(activeSessionId, {
      latest_message_excerpt: message.content.replace(/\s+/g, ' ').slice(0, 48),
      latest_message_role: message.role,
      status_preview: message.title || activeSessionSummary?.status_preview || '等待发送',
      last_activity_at: created.updated_at,
      title:
        activeSessionSummary?.title && !['新会话', '默认会话'].includes(activeSessionSummary.title)
          ? activeSessionSummary.title
          : message.role === 'user'
            ? message.content.replace(/\s+/g, ' ').slice(0, 24) || '新会话'
            : activeSessionSummary?.title || '新会话',
    })
    return mapped
  }, [activeSessionId, activeSessionSummary, applySessionSummaryPatch, mapSessionMessage, projectId])

  const patchPersistedMessage = useCallback(async (
    messageId: string,
    patch: { title?: string; content?: string; payload?: AutoChatMessagePayload },
  ) => {
    if (!activeSessionId) return null
    const updated = await updateAutoSessionMessage(projectId, activeSessionId, messageId, patch)
    const mapped = mapSessionMessage(updated)
    setMessages((prev) => prev.map((item) => (item.id === mapped.id ? mapped : item)))
    return mapped
  }, [activeSessionId, mapSessionMessage, projectId])

  useEffect(() => {
    if (!activeSessionId || hydratingSessionRef.current) return
    if (sessionPatchTimerRef.current) {
      window.clearTimeout(sessionPatchTimerRef.current)
    }
    sessionPatchTimerRef.current = window.setTimeout(() => {
      updateAutoSession(projectId, activeSessionId, {
        draft_script: script || null,
        background_template_id: backgroundTemplateId,
        reference_video_id: referenceVideoId,
        video_platform: videoPlatform,
        video_no_audio: videoNoAudio,
        duration_mode: durationMode,
        video_transition: videoTransition,
        bgm_mood: bgmMood,
        watermark_id: watermarkId,
        current_run_id: currentRun?.id || null,
        status_preview: activeSessionSummary?.status_preview || runStatusText,
        last_activity_at: new Date().toISOString(),
      }).then((detail) => {
        applySessionSummaryPatch(activeSessionId, detail.session)
      }).catch(() => {})
    }, 450)
    return () => {
      if (sessionPatchTimerRef.current) {
        window.clearTimeout(sessionPatchTimerRef.current)
      }
    }
  }, [
    activeSessionId,
    activeSessionSummary?.status_preview,
    applySessionSummaryPatch,
    backgroundTemplateId,
    bgmMood,
    currentRun?.id,
    durationMode,
    projectId,
    referenceVideoId,
    runStatusText,
    script,
    videoNoAudio,
    videoPlatform,
    videoTransition,
    watermarkId,
  ])

  useEffect(() => {
    if (!currentRun || currentRun.status !== 'completed' || !currentRun.final_video_path) {
      setDeliveryInfo(null)
      setLatestPublishDraft(null)
      return
    }
    let cancelled = false
    const loadDelivery = async () => {
      try {
        const info = await getPipelineDelivery(projectId, currentRun.id)
        if (!cancelled) {
          setDeliveryInfo(info)
          setConnectedSocialAccounts(info.connected_social_accounts || [])
          setRecommendedPublishAccount(info.recommended_publish_account || null)
          setLatestPublishDraft(info.latest_publish_draft || null)
          setSelectedPublishAccountId((prev) => prev || info.latest_publish_draft?.social_account_id || info.recommended_publish_account?.id || info.connected_social_accounts?.[0]?.id || null)
        }
      } catch {
        if (!cancelled) setDeliveryInfo(null)
      }
    }
    loadDelivery().catch(() => {})
    const retryTimer = window.setTimeout(() => {
      loadDelivery().catch(() => {})
    }, 1500)
    return () => {
      cancelled = true
      window.clearTimeout(retryTimer)
    }
  }, [projectId, currentRun?.final_video_path, currentRun?.id, currentRun?.status])

  const refreshSocialAccountState = useCallback(async () => {
    try {
      const accounts = await listSocialAccounts()
      setConnectedSocialAccounts(accounts)
      const recommended = accounts.find((item) => item.is_default) || accounts[0] || null
      setRecommendedPublishAccount(recommended)
      setSelectedPublishAccountId((prev) => prev || recommended?.id || null)
    } catch {}
  }, [])

  const reloadActiveSession = useCallback(async () => {
    if (!activeSessionId) return
    const detail = await getAutoSession(projectId, activeSessionId)
    hydrateFromSessionDetail(detail)
  }, [activeSessionId, hydrateFromSessionDetail, projectId])

  useEffect(() => {
    onRegisterOpenPicker?.(() => setShowPicker(true))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onRegisterOpenPicker])

  useEffect(() => {
    listBackgroundTemplates().then(setBackgroundTemplates).catch(() => {})
    listProjects().then(setProjects).catch(() => {})
    refreshSocialAccountState().catch(() => {})
    refreshSessionList().catch(() => {})
  }, [refreshSessionList, refreshSocialAccountState])

  useEffect(() => {
    const handleOauthMessage = (event: MessageEvent) => {
      if (event.data?.type !== 'vidgen-douyin-oauth') return
      if (event.data.success) {
        toast('success', event.data.message || '抖音账号已连接')
        refreshSocialAccountState().catch(() => {})
        reloadActiveSession().catch(() => {})
      } else {
        toast('error', event.data.message || '抖音授权失败')
      }
      setConnectingDouyin(false)
    }
    window.addEventListener('message', handleOauthMessage)
    return () => window.removeEventListener('message', handleOauthMessage)
  }, [refreshSocialAccountState, reloadActiveSession, toast])

  const handleConnectDouyin = useCallback(async () => {
    setConnectingDouyin(true)
    try {
      const { authorization_url } = await startDouyinConnect()
      const popup = window.open(authorization_url, 'vidgen-douyin-oauth', 'width=520,height=720')
      if (!popup) {
        window.location.href = authorization_url
        return
      }
      popup.focus()
    } catch (error: any) {
      setConnectingDouyin(false)
      toast('error', error?.userMessage || '发起抖音授权失败')
    }
  }, [toast])

  useEffect(() => {
    if (
      !activeSessionId
      || !currentRun
      || currentRun.status !== 'completed'
      || !currentRun.final_video_path
      || videoPlatform !== 'douyin'
      || connectedSocialAccounts.length === 0
    ) {
      draftingRunIdRef.current = null
      return
    }
    if (latestPublishDraft?.pipeline_run_id === currentRun.id) {
      draftingRunIdRef.current = currentRun.id
      return
    }
    if (draftingRunIdRef.current === currentRun.id || draftingPublish) {
      return
    }

    draftingRunIdRef.current = currentRun.id
    setDraftingPublish(true)
    createAutoSessionPublishDraft(projectId, activeSessionId, {
      platform: 'douyin',
      social_account_id: selectedPublishAccountId || recommendedPublishAccount?.id || connectedSocialAccounts[0]?.id || null,
    }).then(async () => {
      await reloadActiveSession()
      const info = await getPipelineDelivery(projectId, currentRun.id)
      setDeliveryInfo(info)
      setLatestPublishDraft(info.latest_publish_draft || null)
    }).catch((error: any) => {
      draftingRunIdRef.current = null
      toast('error', error?.userMessage || '生成抖音发布草稿失败')
    }).finally(() => {
      setDraftingPublish(false)
    })
  }, [
    activeSessionId,
    connectedSocialAccounts,
    currentRun,
    draftingPublish,
    latestPublishDraft?.pipeline_run_id,
    projectId,
    recommendedPublishAccount?.id,
    reloadActiveSession,
    selectedPublishAccountId,
    toast,
    videoPlatform,
  ])

  useEffect(() => {
    if (!currentRun) {
      if (sseRef.current) {
        sseRef.current.close()
        sseRef.current = null
      }
      return
    }
    if (sseRef.current) {
      sseRef.current.close()
      sseRef.current = null
    }

    const es = streamPipeline(projectId, currentRun.id)
    sseRef.current = es

    const handleUpdate = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as { run: typeof currentRun; agents: AgentExecution[] }
        setCurrentRun(data.run)
        setAgentExecutions(data.agents)
        if (activeSessionId) {
          applySessionSummaryPatch(activeSessionId, {
            current_run_id: data.run.id,
            current_run_status: data.run.status,
            status_preview: summarizeStatus(data.run),
            last_activity_at: new Date().toISOString(),
          })
        }
      } catch {}
    }

    const handleDone = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as { run: typeof currentRun; agents: AgentExecution[] }
        setCurrentRun(data.run)
        setAgentExecutions(data.agents)
        if (activeSessionId) {
          applySessionSummaryPatch(activeSessionId, {
            current_run_id: data.run.id,
            current_run_status: data.run.status,
            status_preview: summarizeStatus(data.run),
            last_activity_at: new Date().toISOString(),
          })
        }
      } catch {}
      es.close()
      sseRef.current = null
      getPipelineUsage(projectId, currentRun.id).then(setUsageSummary).catch(() => {})
    }

    const handleError = () => {
      es.close()
      sseRef.current = null
      getPipelineRun(projectId, currentRun.id).then(setCurrentRun).catch(() => {})
      getPipelineAgents(projectId, currentRun.id).then(setAgentExecutions).catch(() => {})
    }

    es.addEventListener('update', handleUpdate)
    es.addEventListener('done', handleDone)
    es.addEventListener('error', handleError)

    return () => {
      es.removeEventListener('update', handleUpdate)
      es.removeEventListener('done', handleDone)
      es.removeEventListener('error', handleError)
      es.close()
      sseRef.current = null
    }
  }, [activeSessionId, applySessionSummaryPatch, currentRun?.id, projectId, setAgentExecutions, setCurrentRun, setUsageSummary])

  useEffect(() => {
    if (!analysisMessageIdRef.current) return
    const progressLine = orchestratorExecution?.progress_text?.trim()
    if (!progressLine || progressLine === lastAnalysisLineRef.current) return
    const target = messages.find((message) => message.id === analysisMessageIdRef.current)
    if (!target) return
    const existing = target.mutedLines || []
    if (existing.includes(progressLine)) return
    lastAnalysisLineRef.current = progressLine
    const nextMutedLines = [...existing, progressLine]
    setMessages((prev) => prev.map((message) => (
      message.id === analysisMessageIdRef.current
        ? { ...message, mutedLines: nextMutedLines }
        : message
    )))
    patchPersistedMessage(analysisMessageIdRef.current, {
      payload: {
        mutedLines: nextMutedLines,
        images: target.images,
        files: target.files,
        video: target.video || null,
      },
    }).catch(() => {})
  }, [messages, orchestratorExecution?.progress_text, patchPersistedMessage])

  useEffect(() => {
    if (currentRun?.status !== 'waiting_confirmation' || !replicationOutput) return

    const keyframeImages = buildReplicationFrameImages(replicationOutput)
    const analysisReport = buildReplicationAnalysisReport(replicationOutput)
    const analysisMessageId = analysisMessageIdRef.current
    const reportMessageId = analysisReportMessageIdRef.current
    const currentMessages = messagesRef.current
    const analysisMessage = analysisMessageId ? currentMessages.find((message) => message.id === analysisMessageId) || null : null
    const reportMessage = reportMessageId ? currentMessages.find((message) => message.id === reportMessageId) || null : null

    if (analysisMessage && keyframeImages.length > 0 && !sameMessageImages(analysisMessage.images, keyframeImages)) {
      setMessages((prev) => prev.map((message) => (
        message.id === analysisMessage.id
          ? { ...message, images: keyframeImages }
          : message
      )))
      patchPersistedMessage(analysisMessage.id, {
        payload: {
          mutedLines: analysisMessage.mutedLines,
          images: keyframeImages,
          files: analysisMessage.files,
          video: analysisMessage.video || null,
        },
      }).catch(() => {})
    }

    if (!analysisReport) return

    if (reportMessage) {
      analysisReportMessageIdRef.current = reportMessage.id
      if (reportMessage.content === analysisReport && sameMessageImages(reportMessage.images, keyframeImages)) {
        return
      }
      setMessages((prev) => prev.map((message) => (
        message.id === reportMessage.id
          ? {
              ...message,
              content: analysisReport,
              images: keyframeImages.length > 0 ? keyframeImages : undefined,
            }
          : message
      )))
      patchPersistedMessage(reportMessage.id, {
        content: analysisReport,
        payload: {
          mutedLines: reportMessage.mutedLines,
          images: keyframeImages,
          files: reportMessage.files,
          video: reportMessage.video || null,
        },
      }).catch(() => {})
      return
    }

    // Keep only one module in replication flow: if there is no existing
    // analysis/report card to patch, skip appending a new report card.
    if (!analysisMessage && !reportMessage) return

    if (analysisReportAppendingRef.current) return
    analysisReportAppendingRef.current = true

    appendPersistedMessage({
      role: 'assistant',
      title: '上传视频解析报告',
      content: analysisReport,
      images: keyframeImages,
    }).then((created) => {
      analysisReportMessageIdRef.current = created?.id || null
    }).catch(() => {
      analysisReportAppendingRef.current = false
    })
  }, [appendPersistedMessage, currentRun?.status, patchPersistedMessage, replicationOutput])

  // Show analysis report when pipeline completes in analysis-only mode.
  // Patch the existing initial message instead of appending a new one so only
  // a single assistant message appears for the whole analysis flow.
  useEffect(() => {
    if (currentRun?.status !== 'completed' || !replicationOutput?.analysis_only) return
    const analysisReport = replicationOutput.analysis_report as string | undefined
    if (!analysisReport) return

    const currentMessages = messagesRef.current

    // Prefer patching the initial "视频解析进行中" message that was created on send.
    const initialMessageId = analysisMessageIdRef.current
    const initialMessage = initialMessageId
      ? currentMessages.find((m) => m.id === initialMessageId) || null
      : null

    if (initialMessage) {
      if (initialMessage.content === analysisReport && initialMessage.title === '视频分析报告') return
      patchPersistedMessage(initialMessage.id, {
        title: '视频分析报告',
        content: analysisReport,
        payload: { mutedLines: initialMessage.mutedLines, images: [], files: initialMessage.files, video: initialMessage.video || null },
      }).catch(() => {})
      return
    }

    // Fallback: if no initial message exists, patch or append a standalone report message.
    const reportMessageId = analysisReportMessageIdRef.current
    const reportMessage = reportMessageId ? currentMessages.find((m) => m.id === reportMessageId) || null : null

    if (reportMessage) {
      if (reportMessage.content === analysisReport) return
      patchPersistedMessage(reportMessage.id, {
        content: analysisReport,
        payload: { mutedLines: reportMessage.mutedLines, images: [], files: reportMessage.files, video: reportMessage.video || null },
      }).catch(() => {})
      return
    }

    if (analysisReportAppendingRef.current) return
    analysisReportAppendingRef.current = true
    appendPersistedMessage({
      role: 'assistant',
      title: '视频分析报告',
      content: analysisReport,
    }).then((created) => {
      analysisReportMessageIdRef.current = created?.id || null
    }).catch(() => {
      analysisReportAppendingRef.current = false
    })
  }, [appendPersistedMessage, currentRun?.status, patchPersistedMessage, replicationOutput])

  const handleDeselectMaterial = async (item: MaterialItem) => {
    if (!activeSessionId) return
    await deselectAutoSessionMaterial(projectId, activeSessionId, item.id)
    setSelectedIds((prev) => {
      const next = new Set(prev)
      next.delete(item.id)
      return next
    })
    setSelections((prev) => prev.filter((selection) => selection.material_id !== item.id))
  }

  const handleMaterialsFromPicker = async (items: MaterialItem[]) => {
    if (!activeSessionId) return
    const newItems = items.filter((item) => !selectedIds.has(item.id))
    if (newItems.length === 0) return
    try {
      const newSelections = await Promise.all(
        newItems.map((item, idx) => selectAutoSessionMaterial(projectId, activeSessionId, item.id, item.category, selections.length + idx)),
      )
      setSelectedIds((prev) => {
        const next = new Set(prev)
        newItems.forEach((item) => next.add(item.id))
        return next
      })
      setSelections((prev) => [...prev, ...newSelections])
      await appendPersistedMessage({
        role: 'assistant',
        title: '素材已选中',
        content: `已选入 ${newItems.length} 张素材，现在可以输入脚本并发送生成。`,
        images: newItems.map((item) => ({ id: item.id, url: item.thumbnail_url || '', name: item.filename })),
      })
      setShowPicker(false)
    } catch {
      toast('error', '选择素材失败')
    }
  }

  const handleReferenceVideoSelected = useCallback(async (upload: VideoUpload) => {
    if (!activeSessionId) return
    const filename = upload.filename || '参考视频'
    setCurrentRun(null)
    setAgentExecutions([])
    setUsageSummary(null)
    setDeliveryInfo(null)
    setShowAdjustInput(false)
    setAdjustmentText('')
    analysisMessageIdRef.current = null
    analysisReportMessageIdRef.current = null
    replicationPlanMessageIdRef.current = null
    analysisReportAppendingRef.current = false
    replicationPlanAppendingRef.current = false
    lastAnalysisLineRef.current = null
    setReferenceVideoId(upload.id)
    setReferenceVideoName(filename)
    applySessionSummaryPatch(activeSessionId, {
      reference_video_name: filename,
      title:
        activeSessionSummary?.title && !['新会话', '默认会话'].includes(activeSessionSummary.title)
          ? activeSessionSummary.title
          : filename.slice(0, 24),
      last_activity_at: new Date().toISOString(),
    })
    updateAutoSession(projectId, activeSessionId, {
      current_run_id: null,
      status_preview: '参考视频已上传',
      reference_video_id: upload.id,
      last_activity_at: new Date().toISOString(),
    }).catch(() => {})
  }, [
    activeSessionId,
    activeSessionSummary,
    applySessionSummaryPatch,
    projectId,
    setAgentExecutions,
    setCurrentRun,
    setUsageSummary,
  ])

  const handleGenerateScript = async () => {
    if (selections.length === 0 || !activeSessionId) return
    setGeneratingScript(true)
    const imageIds = selections.map((selection) => selection.material_id)
    const msgImages = selectedMaterials.map((item) => ({ id: item.id, url: item.thumbnail_url || '', name: item.filename }))
    await appendPersistedMessage({
      role: 'user',
      title: '请求 AI 生成脚本',
      content: `已选 ${imageIds.length} 张素材，请根据图片内容生成脚本。`,
      images: msgImages,
    })
    try {
      const result = await generateScript(projectId, imageIds)
      setScript(result.script)
      await appendPersistedMessage({
        role: 'assistant',
        title: 'AI 脚本建议',
        content: `已根据你的素材生成了以下脚本，已自动填入输入框，你可以修改后发送：\n\n${result.script}`,
      })
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '未知错误'
      await appendPersistedMessage({
        role: 'assistant',
        title: '脚本生成失败',
        content: `抱歉，AI 脚本生成失败：${msg}`,
      })
    } finally {
      setGeneratingScript(false)
    }
  }

  const handleSend = async () => {
    if (!activeSessionId) return
    const trimmed = script.trim()
    // When a reference video is uploaded, only require text input — the agent decides what to do.
    // Without a reference video, both script and image selections are required for generation.
    if (hasReferenceVideo) {
      if (!trimmed) return
    } else {
      if (!trimmed || selections.length === 0) return
    }

    setLaunching(true)
    setPreflightWarning(null)
    setCurrentRun(null)
    setAgentExecutions([])
    setUsageSummary(null)
    setDeliveryInfo(null)
    setShowAdjustInput(false)
    setAdjustmentText('')
    analysisReportMessageIdRef.current = null
    replicationPlanMessageIdRef.current = null
    analysisReportAppendingRef.current = false
    replicationPlanAppendingRef.current = false
    const imageIds = selections.map((selection) => selection.material_id)
    const msgImages = selectedMaterials.map((item) => ({ id: item.id, url: item.thumbnail_url || '', name: item.filename }))
    const msgFiles = hasReferenceVideo && referenceVideoId && referenceVideoName
      ? [{
          id: referenceVideoId,
          name: referenceVideoName,
          url: getVideoStreamUrl(referenceVideoId),
          mimeType: 'video/*',
        }]
      : undefined
    const autoDuration = Math.max(imageIds.length * 5, 15)

    if (!hasReferenceVideo) {
      try {
        const check = await preflightCheck(projectId, {
          script: trimmed,
          image_count: imageIds.length,
          duration_seconds: autoDuration,
          duration_mode: durationMode,
        })
        if (!check.ok) {
          setPreflightWarning(check)
          setLaunching(false)
          return
        }
      } catch {}
    }

    await appendPersistedMessage({
      role: 'user',
      title: hasReferenceVideo ? '视频需求' : '用户脚本',
      content: trimmed,
      images: msgImages,
      files: msgFiles,
    })

    if (hasReferenceVideo) {
      // Keep a single visible module for reference-video requests.
      analysisMessageIdRef.current = null
      lastAnalysisLineRef.current = null
    } else {
      await appendPersistedMessage({
        role: 'assistant',
        title: '调度已开始',
        content: '已收到你的脚本和图片素材，我正在安排调度 Agent 启动整条一键生成流水线。下方会用 Agent 节点和进度条持续展示当前进度，并仅显示适合给用户看的阶段输出。',
      })
    }

    try {
      const run = await launchPipeline(projectId, {
        script: trimmed,
        image_ids: imageIds,
        session_id: activeSessionId,
        reference_video_id: hasReferenceVideo ? referenceVideoId : undefined,
        background_template_id: backgroundTemplateId,
        platform: videoPlatform,
        duration_seconds: autoDuration,
        duration_mode: durationMode,
        no_audio: videoNoAudio,
        style: 'commercial',
        voice_id: 'Chelsie',
        transition: videoTransition,
        transition_duration: 0.5,
        bgm_mood: bgmMood,
        bgm_volume: 0.15,
        watermark_image_id: watermarkId,
      })
      setCurrentRun(run)
      setScript('')
      applySessionSummaryPatch(activeSessionId, {
        current_run_id: run.id,
        current_run_status: run.status,
        status_preview: hasReferenceVideo ? '分析中' : '生成中',
        last_activity_at: new Date().toISOString(),
      })
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '未知错误'
      await appendPersistedMessage({
        role: 'assistant',
        title: '启动失败',
        content: `抱歉，启动失败：${msg}`,
      })
    } finally {
      setLaunching(false)
    }
  }

  const handleConfirmPlan = async (approved: boolean) => {
    if (!currentRun) return
    setConfirmingPlan(true)
    try {
      await confirmReplicationPlan(
        projectId,
        currentRun.id,
        approved,
        approved ? undefined : adjustmentText || undefined,
      )
      if (approved) {
        await appendPersistedMessage({
          role: 'user',
          title: '确认执行',
          content: '复刻方案已确认，继续生成视频。',
        })
      } else if (adjustmentText) {
        await appendPersistedMessage({
          role: 'user',
          title: '调整方案',
          content: `调整意见：${adjustmentText}`,
        })
        setAdjustmentText('')
        setShowAdjustInput(false)
      }
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '未知错误'
      toast('error', `操作失败：${msg}`)
    } finally {
      setConfirmingPlan(false)
    }
  }

  const handleTerminatePlan = async () => {
    if (!currentRun) return
    setTerminatingPlan(true)
    try {
      await cancelPipeline(projectId, currentRun.id)
      // Close the SSE immediately so stale in-flight 'update: waiting_confirmation'
      // events cannot overwrite the cancelled state we're about to set.
      if (sseRef.current) {
        sseRef.current.close()
        sseRef.current = null
      }
      const refreshedRun = await getPipelineRun(projectId, currentRun.id)
      setCurrentRun(refreshedRun)
      setShowAdjustInput(false)
      setAdjustmentText('')
      await appendPersistedMessage({
        role: 'user',
        title: '终止本次对话',
        content: '我先终止这次复刻执行，不继续进入生成阶段。',
      })
      await appendPersistedMessage({
        role: 'assistant',
        title: '已终止复刻流程',
        content: '本次复刻流程已经终止，当前解析报告和复刻方案会继续保留在对话中，方便你稍后参考或重新开启新的生成。',
      })
      toast('success', '已终止当前复刻流程')
    } catch (error: unknown) {
      const msg = (error as any)?.response?.data?.detail || (error instanceof Error ? error.message : '未知错误')
      toast('error', `终止失败：${msg}`)
    } finally {
      setTerminatingPlan(false)
    }
  }

  const handleRunControlAction = async () => {
    if (!currentRun) return
    if (currentRun.status === 'waiting_confirmation') {
      await handleTerminatePlan()
      return
    }
    await cancelPipeline(projectId, currentRun.id)
    const run = await getPipelineRun(projectId, currentRun.id)
    setCurrentRun(run)
  }

  return (
    <div className="h-full flex bg-[radial-gradient(circle_at_top,_rgba(171,191,125,0.18),_transparent_28%),linear-gradient(180deg,#f8f0e1_0%,#efe3cd_100%)]">
      <aside className="w-[320px] border-r border-[#d9ccb5] bg-[#fff9ef]/80 backdrop-blur shrink-0 flex flex-col">
        <div className="p-4 border-b border-[#dccfb9]">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold text-[#4c3b22]">会话记录</div>
              <div className="text-xs text-[#7f6d4c] mt-1">每个会话都会保存自己的素材、参考视频与生成状态</div>
            </div>
            <MessageSquareText size={16} className="text-[#8ca65c]" />
          </div>
          <div className="grid grid-cols-1 gap-2 mt-4">
            <button
              onClick={async () => {
                setCreatingSession(true)
                try {
                  const detail = await createAutoSession(projectId)
                  setSessionSummaries((prev) => {
                    const next = [detail.session, ...prev.filter((item) => item.id !== detail.session.id)]
                    return next.sort(
                      (a, b) => new Date(b.last_activity_at).getTime() - new Date(a.last_activity_at).getTime(),
                    )
                  })
                  setActiveSessionId(detail.session.id)
                  hydrateFromSessionDetail(detail)
                } catch (error) {
                  const msg = error instanceof Error ? error.message : '未知错误'
                  toast('error', `新建会话失败：${msg}`)
                } finally {
                  setCreatingSession(false)
                }
              }}
              disabled={creatingSession}
              className="rounded-xl border border-[#8ca65c] bg-[#eef5df] px-3 py-2 text-sm text-[#55722f] hover:bg-[#e2edd0] flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {creatingSession ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              新开会话
            </button>
          </div>
          {onSwitchProject && projects.length > 1 && (
            <select
              value={projectId}
              onChange={(event) => {
                const nextProject = projects.find((item) => item.id === event.target.value)
                if (nextProject) onSwitchProject(nextProject)
              }}
              className="mt-3 w-full rounded-xl border border-[#dccfb9] bg-[#fff8ec] px-3 py-2 text-sm text-[#6f5b38] outline-none"
            >
              {projects.map((project) => (
                <option key={project.id} value={project.id}>{project.name}</option>
              ))}
            </select>
          )}
        </div>

        <div className="px-4 py-3 border-b border-[#dccfb9]">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-400">当前素材</div>
          <div className="mt-2 text-sm text-[#6f5b38]">
            已选 {selectedMaterials.length} 张素材
            {referenceVideoName ? ` · 参考视频 ${referenceVideoName}` : ''}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-2">
          {loadingSessions ? (
            <div className="flex items-center justify-center h-32 text-sm text-slate-400">
              <Loader2 size={16} className="animate-spin mr-2" /> 正在加载会话...
            </div>
          ) : sessionSummaries.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-[#dccfb9] bg-[#fffaf1] px-4 py-6 text-sm text-[#7f6d4c]">
              还没有会话，点击上方“新开会话”开始。
            </div>
          ) : (
            sessionSummaries.map((item) => (
              <button
                key={item.id}
                onClick={() => { if (item.id !== activeSessionId) openSession(item.id).catch(() => {}) }}
                className={cn(
                  'w-full rounded-2xl border px-3 py-3 text-left transition-colors',
                  item.id === activeSessionId
                    ? 'border-[#b59a69] bg-[#f6ebd4]'
                    : 'border-[#e2d6c1] bg-[#fffaf1] hover:bg-[#f7ecd8]',
                )}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-medium text-slate-900 truncate">{item.title}</div>
                  <span className="text-[11px] text-slate-400 shrink-0">{item.status_preview}</span>
                </div>
                <div className="mt-1 text-xs text-slate-500 leading-5">
                  {item.latest_message_excerpt || item.reference_video_name || '点击恢复这个会话'}
                </div>
              </button>
            ))
          )}
        </div>
      </aside>

      <section className="flex-1 flex flex-col">

        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
          {loadingSessionDetail && (
            <div className="rounded-2xl border border-[#dccfb9] bg-[#fffaf1] px-4 py-3 text-sm text-[#7f6d4c] flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" /> 正在恢复会话内容...
            </div>
          )}
          {messages.map((message) => {
            const isAssistant = message.role === 'assistant'
            const isUser = message.role === 'user'
            const isLatestAssistant = isAssistant && message.id === latestAssistantMessageId
            const processStage = isAssistant
              ? inferAssistantProcessStage(message, currentRun?.status || null, isLatestAssistant)
              : null
            const processSummary = isAssistant
              ? buildAssistantProcessSummary(message, processStage || 'idle')
              : ''
            const processToolCalls = isAssistant
              ? inferAssistantToolCalls(message)
              : []
            const processTimeline = isAssistant
              ? buildAssistantProcessTimeline(message, processStage || 'idle')
              : []
            const isProcessOpen = isAssistant
              ? (
                processPanelOpenMap[message.id]
                ?? (isLatestAssistant && (processTimeline.length > 0 || processToolCalls.length > 0 || processStage === 'waiting_confirmation'))
              )
              : false
            return (
              <div
                id={message.id}
                key={message.id}
                className={cn(
                  'flex max-w-4xl gap-3',
                  isUser && 'ml-auto justify-end',
                )}
              >
                {isAssistant && <CapyAvatar size="sm" className="mt-1 shrink-0" />}
                <div
                  className={cn(
                    'rounded-3xl px-5 py-4 shadow-sm whitespace-pre-wrap',
                    isAssistant && 'bg-white border border-slate-200 text-slate-700',
                    isUser && 'bg-blue-600 text-white',
                    message.role === 'system' && 'bg-amber-50 border border-amber-200 text-amber-900',
                  )}
                >
                  {isAssistant ? (
                    <>
                      <div className="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-2.5">
                        <div className="flex items-center justify-between gap-3">
                          <div className="text-[11px] uppercase tracking-[0.16em] text-slate-500">过程区</div>
                          <div className="flex items-center gap-2">
                            {processStage && (
                              <span className={cn(
                                'rounded-full px-2 py-0.5 text-[10px] font-medium',
                                processStage === 'Done' && 'bg-emerald-100 text-emerald-700',
                                processStage === 'waiting_confirmation' && 'bg-violet-100 text-violet-700',
                                processStage === 'running' && 'bg-amber-100 text-amber-700',
                                processStage === 'failed' && 'bg-red-100 text-red-700',
                                processStage === 'cancelled' && 'bg-slate-200 text-slate-600',
                                processStage === 'idle' && 'bg-slate-200 text-slate-600',
                              )}>
                                {processStage}
                              </span>
                            )}
                            <button
                              onClick={() => {
                                setProcessPanelOpenMap((prev) => ({ ...prev, [message.id]: !isProcessOpen }))
                              }}
                              className="rounded-full border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-500 hover:bg-slate-100"
                            >
                              {isProcessOpen ? (
                                <span className="flex items-center gap-1"><ChevronUp size={12} /> 折叠</span>
                              ) : (
                                <span className="flex items-center gap-1"><ChevronDown size={12} /> 展开</span>
                              )}
                            </button>
                          </div>
                        </div>
                        {isProcessOpen && (
                          <div className="mt-2 space-y-2">
                            <div className="text-xs text-slate-600">思考摘要：{processSummary}</div>
                            {processToolCalls.length > 0 && (
                              <div className="flex flex-wrap gap-1.5">
                                {processToolCalls.map((tool) => (
                                  <span
                                    key={`${message.id}-tool-${tool}`}
                                    className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] text-slate-600"
                                  >
                                    tool: {tool}
                                  </span>
                                ))}
                              </div>
                            )}
                            {processTimeline.length > 0 && (
                              <div className="space-y-1 rounded-xl border border-slate-200 bg-white px-2.5 py-2">
                                {processTimeline.map((line, index) => (
                                  <div key={`${message.id}-timeline-${index}`} className="text-xs leading-5 text-slate-500">
                                    {index + 1}. {line}
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>

                      <div className="mt-3 rounded-2xl border border-slate-200 bg-white px-3 py-3">
                        <div className="text-[11px] uppercase tracking-[0.16em] text-slate-500 mb-2">正式输出</div>
                        {renderMessageFileAttachments(message.files, 'assistant')}
                        {message.content && (
                          <div className="text-sm leading-6 text-slate-700">{message.content}</div>
                        )}

                        {message.video && (
                          <div className="mt-3 overflow-hidden rounded-2xl border border-slate-200 bg-black">
                            <video src={message.video.streamUrl} controls className="w-full max-h-[320px]" />
                            <div className="border-t border-slate-200 bg-white px-3 py-2 text-xs text-slate-500">
                              参考视频：{message.video.name}
                            </div>
                          </div>
                        )}
                        {message.publishDraft && (
                          <PublishDraftCard
                            draft={message.publishDraft}
                            accounts={connectedSocialAccounts}
                            selectedAccountId={selectedPublishAccountId}
                            connecting={connectingDouyin}
                            publishing={publishingDraftMessageId === message.id}
                            onSelectAccount={(accountId) => setSelectedPublishAccountId(accountId)}
                            onConnectDouyin={handleConnectDouyin}
                            onPublish={async (draftInput) => {
                              if (!currentRun) return
                              if (!draftInput.social_account_id) {
                                toast('error', '请先选择已连接的抖音账号')
                                return
                              }
                              setPublishingDraftMessageId(message.id)
                              try {
                                const record = await publishPipelineVideoToDouyin(projectId, currentRun.id, draftInput)
                                const nextDraft: PublishDraft = {
                                  ...message.publishDraft!,
                                  social_account_id: draftInput.social_account_id,
                                  account_name: connectedSocialAccounts.find((item) => item.id === draftInput.social_account_id)?.display_name || message.publishDraft?.account_name || null,
                                  title: draftInput.title,
                                  description: draftInput.description,
                                  hashtags: draftInput.hashtags || [],
                                  visibility: draftInput.visibility || 'public',
                                  cover_title: draftInput.cover_title || null,
                                  status: record.status,
                                }
                                await patchPersistedMessage(message.id, {
                                  payload: {
                                    mutedLines: message.mutedLines,
                                    images: message.images,
                                    files: message.files,
                                    video: message.video || null,
                                    publishDraft: nextDraft,
                                  },
                                })
                                await appendPersistedMessage({
                                  role: 'assistant',
                                  title: '抖音发布已提交',
                                  content: '我已经按你确认后的草稿向抖音提交发布请求，你可以在交付记录里继续查看返回状态。',
                                })
                                const info = await getPipelineDelivery(projectId, currentRun.id)
                                setDeliveryInfo(info)
                                setLatestPublishDraft(info.latest_publish_draft || nextDraft)
                                toast('success', '已向抖音提交发布请求')
                              } catch (error: any) {
                                toast('error', error?.userMessage || '发布到抖音失败')
                              } finally {
                                setPublishingDraftMessageId(null)
                              }
                            }}
                          />
                        )}
                        {message.images && message.images.length > 0 && (
                          <div className="flex gap-2 mt-3 overflow-x-auto">
                            {message.images.map((img) => (
                              <div key={img.id} className="w-16 h-12 shrink-0 rounded-lg overflow-hidden border border-white/30 bg-black/10">
                                <img
                                  src={img.url}
                                  alt={img.name}
                                  className="w-full h-full object-cover"
                                  onError={(event) => {
                                    (event.target as HTMLImageElement).style.visibility = 'hidden'
                                  }}
                                />
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      {isLatestAssistant && usageSummary && (
                        <div className="mt-2 px-1 text-xs text-slate-500">
                          已统计 Tokens：{usageSummary.total_tokens.toLocaleString()}，模型调用 {usageSummary.request_count} 次
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="text-xs uppercase tracking-[0.18em] opacity-60 mb-2">
                        {message.title || (isUser ? '用户输入' : '系统消息')}
                      </div>
                      {renderMessageFileAttachments(message.files, isUser ? 'user' : 'assistant')}
                      {message.content && (
                        <div className="text-sm leading-6">{message.content}</div>
                      )}
                      {message.video && (
                        <div className="mt-3 overflow-hidden rounded-2xl border border-slate-200 bg-black">
                          <video src={message.video.streamUrl} controls className="w-full max-h-[320px]" />
                          <div className="border-t border-slate-200 bg-white px-3 py-2 text-xs text-slate-500">
                            参考视频：{message.video.name}
                          </div>
                        </div>
                      )}
                      {message.images && message.images.length > 0 && (
                        <div className="flex gap-2 mt-3 overflow-x-auto">
                          {message.images.map((img) => (
                            <div key={img.id} className="w-16 h-12 shrink-0 rounded-lg overflow-hidden border border-white/30 bg-black/10">
                              <img
                                src={img.url}
                                alt={img.name}
                                className="w-full h-full object-cover"
                                onError={(event) => {
                                  (event.target as HTMLImageElement).style.visibility = 'hidden'
                                }}
                              />
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            )
          })}

          {currentRun?.status === 'waiting_confirmation' && replicationPlan && (
            <div className="max-w-4xl overflow-hidden rounded-3xl border border-violet-200 bg-violet-50 shadow-sm">
              <div className="border-b border-violet-200 px-5 py-4">
                <div className="text-xs uppercase tracking-[0.18em] text-violet-500">复刻方案确认</div>
                <div className="mt-2 text-sm leading-6 text-slate-700">
                  方案已经整理完成。你可以直接确认执行，也可以先补充调整意见后再继续。
                </div>
              </div>

              <div className="max-h-[60vh] overflow-y-auto px-5 py-4">
                <div className="space-y-4">
                  {replicationPlan.video_summary && (
                    <section className="rounded-2xl border border-violet-100 bg-white px-4 py-3">
                      <div className="text-xs uppercase tracking-[0.16em] text-violet-500">内容目标</div>
                      <div className="mt-2 text-sm leading-6 text-slate-700">{String(replicationPlan.video_summary)}</div>
                    </section>
                  )}

                  {(replicationPlan.overall_style || replicationPlan.color_palette || replicationPlan.pacing) && (
                    <section className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                      <div className="text-xs uppercase tracking-[0.16em] text-slate-500">整体设计</div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {replicationPlan.overall_style && (
                          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">
                            整体风格：{String(replicationPlan.overall_style)}
                          </span>
                        )}
                        {replicationPlan.color_palette && (
                          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">
                            色彩基调：{String(replicationPlan.color_palette)}
                          </span>
                        )}
                        {replicationPlan.pacing && (
                          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">
                            节奏特征：{String(replicationPlan.pacing)}
                          </span>
                        )}
                      </div>
                    </section>
                  )}

                  {replicationOutput?.background_context && (
                    <section className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                      <div className="text-xs uppercase tracking-[0.16em] text-slate-500">背景信息约束</div>
                      <div className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">
                        {String(replicationOutput.background_context)}
                      </div>
                    </section>
                  )}

                  {(hasReplicationDesignDetails(replicationPlan.audio_design) || hasReplicationDesignDetails(replicationPlan.music_design)) && (
                    <div className="grid gap-4 md:grid-cols-2">
                      {hasReplicationDesignDetails(replicationPlan.audio_design) && (
                        <section className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                          <div className="text-xs uppercase tracking-[0.16em] text-slate-500">音频设计</div>
                          <div className="mt-2 space-y-1 text-sm leading-6 text-slate-700">
                            {replicationPlan.audio_design.voice_style && <div>音色：{String(replicationPlan.audio_design.voice_style)}</div>}
                            {replicationPlan.audio_design.voice_speed !== undefined && replicationPlan.audio_design.voice_speed !== null && (
                              <div>语速：{String(replicationPlan.audio_design.voice_speed)}</div>
                            )}
                            {replicationPlan.audio_design.voice_tone && <div>语气：{String(replicationPlan.audio_design.voice_tone)}</div>}
                            {replicationPlan.audio_design.narration_notes && (
                              <div className="text-slate-500">备注：{String(replicationPlan.audio_design.narration_notes)}</div>
                            )}
                          </div>
                        </section>
                      )}

                      {hasReplicationDesignDetails(replicationPlan.music_design) && (
                        <section className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                          <div className="text-xs uppercase tracking-[0.16em] text-slate-500">音乐设计</div>
                          <div className="mt-2 space-y-1 text-sm leading-6 text-slate-700">
                            {replicationPlan.music_design.bgm_mood && <div>情绪：{String(replicationPlan.music_design.bgm_mood)}</div>}
                            {replicationPlan.music_design.bgm_style && <div>风格：{String(replicationPlan.music_design.bgm_style)}</div>}
                            {replicationPlan.music_design.volume_level && <div>音量：{String(replicationPlan.music_design.volume_level)}</div>}
                            {replicationPlan.music_design.music_notes && (
                              <div className="text-slate-500">备注：{String(replicationPlan.music_design.music_notes)}</div>
                            )}
                          </div>
                        </section>
                      )}
                    </div>
                  )}

                  {Array.isArray(replicationPlan.shots) && replicationPlan.shots.length > 0 && (
                    <section className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                      <div className="text-xs uppercase tracking-[0.16em] text-slate-500">镜头方案</div>
                      <div className="mt-3 space-y-3">
                        {replicationPlan.shots.map((shot: Record<string, any>, index: number) => {
                          const shotNumber = (shot.shot_idx ?? index) + 1
                          const previewUrl = getReplicationShotPreviewUrl(shot)
                          const previewName = getReplicationShotPreviewName(shot, shotNumber)
                          const previewBadge = getReplicationShotPreviewBadge(shot)
                          const durationLabel = formatReplicationShotDuration(shot.suggested_duration_seconds)
                          const timestampLabel = formatReplicationTimestampRange(shot.timestamp_range)

                          return (
                            <div key={`replication-shot-${shot.shot_idx ?? index}`} className="flex gap-3 rounded-2xl border border-slate-200 bg-slate-50/70 px-3 py-3">
                              {previewUrl ? (
                                <div className="shrink-0">
                                  <div className="h-12 w-16 overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
                                    <img
                                      src={previewUrl}
                                      alt={previewName}
                                      className="h-full w-full object-cover"
                                      onError={(event) => {
                                        (event.target as HTMLImageElement).style.visibility = 'hidden'
                                      }}
                                    />
                                  </div>
                                  {previewBadge && (
                                    <div className="mt-1 text-center text-[11px] text-slate-400">{previewBadge}</div>
                                  )}
                                </div>
                              ) : (
                                <div className="flex h-12 w-16 shrink-0 items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white text-[11px] text-slate-400">
                                  无预览
                                </div>
                              )}

                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-start justify-between gap-2">
                                  <div className="text-sm font-medium text-slate-900">镜头 {shotNumber}</div>
                                  {durationLabel && (
                                    <span className="rounded-full bg-violet-100 px-2.5 py-1 text-[11px] text-violet-700">
                                      {durationLabel}
                                    </span>
                                  )}
                                </div>
                                <div className="mt-1 text-sm leading-6 text-slate-700">
                                  {shot.description ? String(shot.description) : '未提供描述'}
                                </div>
                                <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
                                  {shot.camera_movement && (
                                    <span className="rounded-full bg-white px-2.5 py-1">运镜：{String(shot.camera_movement)}</span>
                                  )}
                                  {shot.visual_design && (
                                    <span className="rounded-full bg-white px-2.5 py-1">画面：{String(shot.visual_design)}</span>
                                  )}
                                  {Array.isArray(shot.subjects) && shot.subjects.length > 0 && (
                                    <span className="rounded-full bg-white px-2.5 py-1">
                                      主体：{shot.subjects.map((subject: unknown) => String(subject)).join('、')}
                                    </span>
                                  )}
                                  {timestampLabel && (
                                    <span className="rounded-full bg-white px-2.5 py-1">参考：{timestampLabel}</span>
                                  )}
                                </div>
                                {typeof shot.material_filename === 'string' && shot.material_filename && (
                                  <div className="mt-2 truncate text-xs text-slate-400">素材：{shot.material_filename}</div>
                                )}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </section>
                  )}
                </div>
              </div>

              <div className="border-t border-violet-200 bg-white/80 px-5 py-4">
                {showAdjustInput && (
                  <div className="mb-3">
                    <textarea
                      value={adjustmentText}
                      onChange={(event) => setAdjustmentText(event.target.value)}
                      placeholder="请描述你希望如何调整复刻方案…"
                      rows={2}
                      className="w-full rounded-xl border border-violet-200 px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-violet-300"
                    />
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => handleConfirmPlan(true).catch(() => {})}
                    disabled={confirmingPlan || terminatingPlan}
                    className="flex items-center gap-2 rounded-full bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
                  >
                    {confirmingPlan ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                    确认执行
                  </button>
                  {!showAdjustInput ? (
                    <button
                      onClick={() => setShowAdjustInput(true)}
                      className="flex items-center gap-2 rounded-full border border-violet-300 px-4 py-2 text-sm text-violet-700 hover:bg-violet-50"
                    >
                      调整方案
                    </button>
                  ) : (
                    <>
                      <button
                        onClick={() => handleConfirmPlan(false).catch(() => {})}
                        disabled={confirmingPlan || terminatingPlan || !adjustmentText.trim()}
                        className="flex items-center gap-2 rounded-full border border-violet-300 px-4 py-2 text-sm text-violet-700 hover:bg-violet-50 disabled:opacity-50"
                      >
                        {confirmingPlan ? <Loader2 size={14} className="animate-spin" /> : null}
                        提交调整
                      </button>
                      <button
                        onClick={() => { setShowAdjustInput(false); setAdjustmentText('') }}
                        className="rounded-full border border-slate-200 px-3 py-2 text-sm text-slate-500 hover:bg-slate-50"
                      >
                        <X size={14} />
                      </button>
                    </>
                  )}
                  <button
                    onClick={() => handleTerminatePlan().catch(() => {})}
                    disabled={confirmingPlan || terminatingPlan}
                    className="flex items-center gap-2 rounded-full border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-600 hover:bg-red-100 disabled:opacity-50"
                  >
                    {terminatingPlan ? <Loader2 size={14} className="animate-spin" /> : <StopCircle size={14} />}
                    终止本次对话
                  </button>
                </div>
              </div>
            </div>
          )}

          {currentRun && (
            <PipelineNodeBoard
              projectId={projectId}
              runId={currentRun.id}
              runStatus={currentRun.status}
              currentExecution={currentExecution}
              completedExecutions={completedExecutions}
              finalVideoPath={currentRun.final_video_path}
              deliveryInfo={deliveryInfo}
              onDeliveryInfoChange={setDeliveryInfo}
              onConnectDouyin={handleConnectDouyin}
              connectingDouyin={connectingDouyin}
              draftingPublish={draftingPublish}
              onRetry={async () => {
                try {
                  const updated = await retryFailedAgent(projectId, currentRun.id)
                  setCurrentRun(updated)
                } catch (error: any) {
                  await appendPersistedMessage({
                    role: 'assistant',
                    title: '重试失败',
                    content: `重试失败：${error?.response?.data?.detail || error.message}`,
                  })
                }
              }}
            />
          )}
        </div>

        <div className="px-6 py-5 border-t border-slate-200 bg-white/75 backdrop-blur space-y-4">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <MessageSquareText size={16} />
            已选素材 {selectedMaterials.length} 张
          </div>

          {selectedMaterials.length > 0 && (
            <div className="flex gap-3 overflow-x-auto pb-1">
              {selectedMaterials.map((item) => (
                <div key={item.id} className="w-20 shrink-0 group relative">
                  <div className="w-20 h-16 rounded-2xl overflow-hidden border border-slate-200 bg-slate-100 relative">
                    <img src={item.thumbnail_url || ''} alt={item.filename} className="w-full h-full object-cover" />
                    <button
                      onClick={() => handleDeselectMaterial(item).catch(() => {})}
                      className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-slate-700/80 text-white flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-500"
                    >
                      <X size={12} />
                    </button>
                  </div>
                  <div className="text-[11px] text-slate-500 truncate mt-1">{item.filename}</div>
                </div>
              ))}
            </div>
          )}

          <div className="rounded-3xl border border-slate-200 bg-white shadow-sm p-3">
            {referenceVideoId && referenceVideoName && (
              <div className="mb-2 flex flex-wrap gap-1.5">
                <span className="inline-flex max-w-[260px] items-center gap-1.5 rounded-lg bg-slate-100 pl-1.5 pr-1 py-1 text-xs text-slate-700">
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-amber-100 text-amber-600">
                    <Video size={11} />
                  </span>
                  <span className="truncate font-medium">{referenceVideoName}</span>
                  <button
                    type="button"
                    onClick={() => {
                      setReferenceVideoId(null)
                      setReferenceVideoName(null)
                      if (activeSessionId) {
                        updateAutoSession(projectId, activeSessionId, {
                          reference_video_id: null,
                          last_activity_at: new Date().toISOString(),
                        }).catch(() => {})
                      }
                    }}
                    className="ml-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded text-slate-400 hover:bg-slate-200 hover:text-slate-600"
                    aria-label="移除参考视频"
                  >
                    <X size={10} />
                  </button>
                </span>
              </div>
            )}
            <textarea
              value={script}
              onChange={(event) => setScript(event.target.value)}
              rows={5}
              className="w-full resize-none outline-none text-sm text-slate-800 placeholder:text-slate-400 bg-transparent"
            />
            <div className="mt-3 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setShowPicker(true)}
                  className="rounded-full px-3 py-2 text-sm text-slate-600 bg-slate-100 hover:bg-slate-200 flex items-center gap-2"
                >
                  <ImagePlus size={14} /> 添加素材
                </button>
                <button
                  onClick={() => handleGenerateScript().catch(() => {})}
                  disabled={generatingScript || selections.length === 0}
                  className="rounded-full px-3 py-2 text-sm text-violet-600 bg-violet-50 hover:bg-violet-100 disabled:opacity-50 flex items-center gap-2"
                >
                  {generatingScript ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                  AI 生成脚本
                </button>
                <div className="flex items-center gap-1.5 text-sm text-slate-600">
                  <span className="text-xs text-slate-400">背景模板</span>
                  <select
                    value={backgroundTemplateId ?? ''}
                    onChange={(event) => setBackgroundTemplateId(event.target.value || null)}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm text-slate-700 outline-none max-w-[160px]"
                  >
                    <option value="">不使用</option>
                    {backgroundTemplates.map((template) => (
                      <option key={template.id} value={template.id}>{template.name}</option>
                    ))}
                  </select>
                </div>
                <div className="flex items-center gap-1.5 text-sm text-slate-600">
                  <span className="text-xs text-slate-400">平台</span>
                  <select
                    value={videoPlatform}
                    onChange={(event) => setVideoPlatform(event.target.value)}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm text-slate-700 outline-none"
                  >
                    <option value="generic">通用 16:9</option>
                    <option value="douyin">抖音 9:16</option>
                    <option value="xiaohongshu">小红书 3:4</option>
                    <option value="bilibili">B站 16:9</option>
                  </select>
                </div>
                <div className="flex items-center gap-1.5 text-sm text-slate-600">
                  <span className="text-xs text-slate-400">时长</span>
                  <select
                    value={durationMode}
                    onChange={(event) => setDurationMode(event.target.value as 'auto' | 'fixed')}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm text-slate-700 outline-none"
                  >
                    <option value="fixed">固定 ({selections.length > 0 ? `${selections.length * 5}s` : '—'})</option>
                    <option value="auto">自动</option>
                  </select>
                  {durationMode === 'fixed' && selections.length > 0 && (
                    <span className="text-xs text-slate-400">({selections.length}张×5s)</span>
                  )}
                  {durationMode === 'auto' && (
                    <span className="text-xs text-slate-400">按语音时长</span>
                  )}
                </div>
                <label className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={!videoNoAudio}
                    onChange={(event) => setVideoNoAudio(!event.target.checked)}
                    className="rounded border-slate-300"
                  />
                  <Volume2 size={14} className={videoNoAudio ? 'text-slate-300' : 'text-violet-500'} />
                  <span className="text-xs text-slate-400">视频原声</span>
                </label>
                <div className="flex items-center gap-1.5 text-sm text-slate-600">
                  <span className="text-xs text-slate-400">转场</span>
                  <select
                    value={videoTransition}
                    onChange={(event) => setVideoTransition(event.target.value)}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm text-slate-700 outline-none"
                  >
                    <option value="none">无</option>
                    <option value="fade">淡入淡出</option>
                    <option value="dissolve">溶解</option>
                    <option value="slideright">右滑</option>
                    <option value="slideup">上推</option>
                  </select>
                </div>
                <div className="flex items-center gap-1.5 text-sm text-slate-600">
                  <span className="text-xs text-slate-400">BGM</span>
                  <select
                    value={bgmMood}
                    onChange={(event) => setBgmMood(event.target.value)}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm text-slate-700 outline-none"
                  >
                    <option value="none">无</option>
                    <option value="upbeat">欢快</option>
                    <option value="calm">舒缓</option>
                    <option value="cinematic">电影感</option>
                    <option value="energetic">动感</option>
                  </select>
                </div>
                <div className="flex items-center gap-1.5 text-sm text-slate-600">
                  <span className="text-xs text-slate-400">水印</span>
                  <select
                    value={watermarkId ?? ''}
                    onChange={(event) => setWatermarkId(event.target.value || null)}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm text-slate-700 outline-none max-w-[120px]"
                  >
                    <option value="">无</option>
                    {selectedMaterials.filter((item) => item.media_type.startsWith('image/')).map((item) => (
                      <option key={item.id} value={item.id}>{item.filename}</option>
                    ))}
                  </select>
                </div>
              </div>
              {hasActiveRunControl ? (
                <button
                  onClick={() => handleRunControlAction().catch(() => {})}
                  disabled={terminatingPlan}
                  className="rounded-full px-5 py-2.5 bg-red-50 text-red-600 hover:bg-red-100 text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                >
                  {terminatingPlan ? <Loader2 size={16} className="animate-spin" /> : <StopCircle size={16} />}
                  {currentRun?.status === 'waiting_confirmation' ? '终止' : '取消'}
                </button>
              ) : (
                <button
                  onClick={() => handleSend().catch(() => {})}
                  disabled={
                    !activeSessionId ||
                    launching ||
                    (referenceVideoId ? !script.trim() : (!script.trim() || selections.length === 0))
                  }
                  className="rounded-full px-5 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                >
                  {launching ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                  发送并生成
                </button>
              )}
            </div>
            {preflightWarning && !preflightWarning.ok && (
              <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-3">
                <AlertTriangle size={18} className="text-amber-500 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-amber-800">{preflightWarning.warning}</div>
                  <div className="mt-2 flex items-center gap-3 text-xs text-amber-600">
                    <span>预估口播 {preflightWarning.estimated_audio_seconds}s</span>
                    <span>·</span>
                    <span>建议素材 {preflightWarning.recommended_image_count} 张</span>
                    {preflightWarning.estimated_tokens > 0 && (
                      <>
                        <span>·</span>
                        <span>预估消耗 ~{(preflightWarning.estimated_tokens / 1000).toFixed(1)}k tokens</span>
                      </>
                    )}
                  </div>
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() => setPreflightWarning(null)}
                      className="rounded-lg bg-amber-100 px-3 py-1 text-xs text-amber-700 hover:bg-amber-200"
                    >
                      我知道了，继续调整
                    </button>
                    <button
                      onClick={() => {
                        setPreflightWarning(null)
                        setLaunching(true)
                        const imageIds = selections.map((selection) => selection.material_id)
                        const msgImages = selectedMaterials.map((item) => ({ id: item.id, url: item.thumbnail_url || '', name: item.filename }))
                        appendPersistedMessage({ role: 'user', title: '用户脚本', content: script.trim(), images: msgImages }).catch(() => {})
                        appendPersistedMessage({ role: 'assistant', title: '调度已开始', content: '已收到你的脚本和图片素材（音频可能超出视频时长，系统会自动调整），正在启动流水线。' }).catch(() => {})
                        launchPipeline(projectId, {
                          script: script.trim(),
                          image_ids: imageIds,
                          session_id: activeSessionId,
                          background_template_id: backgroundTemplateId,
                          platform: videoPlatform,
                          duration_seconds: imageIds.length * 5,
                          duration_mode: durationMode,
                          no_audio: videoNoAudio,
                          style: 'commercial',
                          voice_id: 'Chelsie',
                          transition: videoTransition,
                          transition_duration: 0.5,
                          bgm_mood: bgmMood,
                          bgm_volume: 0.15,
                          watermark_image_id: watermarkId,
                        }).then((run) => {
                          setCurrentRun(run)
                          setScript('')
                        }).catch((error) => {
                          appendPersistedMessage({ role: 'assistant', content: `启动失败：${error?.response?.data?.detail || error.message}` }).catch(() => {})
                        }).finally(() => setLaunching(false))
                      }}
                      className="rounded-lg bg-amber-500 px-3 py-1 text-xs text-white hover:bg-amber-600"
                    >
                      忽略警告，强制发送
                    </button>
                  </div>
                </div>
              </div>
            )}
            {selectedBackgroundTemplate && (
              <div className="mt-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-400">当前背景模板</div>
                <div className="mt-2 text-sm font-medium text-slate-900">{selectedBackgroundTemplate.name}</div>
                <div className="mt-2 whitespace-pre-wrap text-xs leading-6 text-slate-600">
                  {selectedBackgroundTemplate.compiled_background_context}
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      {showPicker && (
        <MaterialPickerModal
          projectId={projectId}
          sessionId={activeSessionId}
          onClose={() => setShowPicker(false)}
          onMaterialsSelected={handleMaterialsFromPicker}
          onVideoSelected={handleReferenceVideoSelected}
        />
      )}
    </div>
  )
}

function renderMessageFileAttachments(
  files: ChatMessage['files'],
  tone: 'user' | 'assistant',
) {
  if (!files?.length) return null

  return (
    <div className="mb-3 flex flex-wrap gap-2">
      {files.map((file) => {
        const ext = file.name.includes('.') ? file.name.split('.').pop()?.toUpperCase() : 'FILE'
        const chipClassName = tone === 'user'
          ? 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
          : 'border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100'
        return (
          <a
            key={`${file.id}-${file.name}`}
            href={file.url}
            target="_blank"
            rel="noreferrer"
            className={cn(
              'inline-flex max-w-full items-center gap-2 rounded-2xl border px-3 py-2 transition-colors',
              chipClassName,
            )}
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-amber-100 text-amber-700">
              <Video size={14} />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">{file.name}</span>
              <span className="block text-[11px] uppercase tracking-[0.08em] text-slate-400">
                {ext || 'FILE'}
              </span>
            </span>
          </a>
        )
      })}
    </div>
  )
}

type AssistantProcessStage = 'Done' | 'waiting_confirmation' | 'running' | 'failed' | 'cancelled' | 'idle'

function inferAssistantProcessStage(
  message: ChatMessage,
  runStatus: string | null,
  isLatestAssistant: boolean,
): AssistantProcessStage {
  const title = (message.title || '').toLowerCase()
  const content = (message.content || '').toLowerCase()
  if (title.includes('失败') || content.includes('失败')) return 'failed'
  if (title.includes('终止') || title.includes('取消') || content.includes('终止')) return 'cancelled'
  if (title.includes('复刻执行方案')) return 'waiting_confirmation'
  if (title.includes('进行中') || title.includes('已开始') || content.includes('正在')) return 'running'
  if (title.includes('已提交') || title.includes('已完成') || content.includes('已完成')) return 'Done'

  if (isLatestAssistant && runStatus) {
    if (runStatus === 'waiting_confirmation') return 'waiting_confirmation'
    if (runStatus === 'running' || runStatus === 'pending') return 'running'
    if (runStatus === 'completed') return 'Done'
    if (runStatus === 'failed') return 'failed'
    if (runStatus === 'cancelled') return 'cancelled'
  }
  return 'idle'
}

function buildAssistantProcessSummary(
  message: ChatMessage,
  stage: AssistantProcessStage,
): string {
  if (message.title) return message.title
  if (stage === 'waiting_confirmation') return '当前输出进入等待确认阶段'
  if (stage === 'running') return '当前输出仍在执行中'
  if (stage === 'Done') return '当前输出已完成'
  return '当前输出已生成'
}

function inferAssistantToolCalls(message: ChatMessage): string[] {
  const lines = message.mutedLines || []
  const merged = `${message.title || ''}\n${lines.join('\n')}\n${message.content || ''}`
  const tools = new Set<string>()

  if (message.video) tools.add('upload_reference_video')
  if (/关键帧|提取帧|scene_change|extract/i.test(merged)) tools.add('extract_keyframes')
  if (/分析|镜头|节奏|结构/i.test(merged)) tools.add('analyze_video_structure')
  if (/复刻方案|执行方案|planning|plan/i.test(merged)) tools.add('build_replication_plan')
  if (/发布草稿|抖音发布|publish/i.test(merged)) tools.add('build_publish_draft')

  return Array.from(tools)
}

function buildAssistantProcessTimeline(
  message: ChatMessage,
  stage: AssistantProcessStage,
): string[] {
  const timeline = [...(message.mutedLines || [])]
  if (stage === 'waiting_confirmation') {
    timeline.push('状态切换为 waiting_confirmation，等待用户确认')
  } else if (stage === 'Done') {
    timeline.push('状态切换为 Done')
  } else if (stage === 'running') {
    timeline.push('状态为 running')
  } else if (stage === 'failed') {
    timeline.push('状态为 failed')
  } else if (stage === 'cancelled') {
    timeline.push('状态为 cancelled')
  }
  return timeline
}

const AGENT_ORDER = [
  'orchestrator',
  'prompt_engineer',
  'audio_subtitle',
  'video_generator',
  'video_editor',
]


function toMediaUrl(path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) return encodeURI(path)
  const normalized = path.replace(/\\/g, '/')
  const repositoryMarker = '/video_repository/'
  const generatedMarker = '/generated/'
  const repoIndex = normalized.lastIndexOf(repositoryMarker)
  if (repoIndex >= 0) {
    return encodeURI(`/repository/${normalized.slice(repoIndex + repositoryMarker.length)}`)
  }
  const generatedIndex = normalized.lastIndexOf(generatedMarker)
  if (generatedIndex >= 0) {
    return encodeURI(`/generated/${normalized.slice(generatedIndex + generatedMarker.length)}`)
  }
  const filename = normalized.split('/').pop() || normalized
  return encodeURI(`/generated/${filename}`)
}

function buildReplicationFrameImages(replicationOutput: Record<string, any> | null): Array<{ id: string; url: string; name: string }> {
  const frames = Array.isArray(replicationOutput?.extracted_frames) ? replicationOutput.extracted_frames : []
  return frames
    .filter((frame) => typeof frame?.frame_path === 'string' && frame.frame_path)
    .map((frame, index) => {
      const timestamp = Number(frame.timestamp_seconds)
      const safeTimestamp = Number.isFinite(timestamp) ? timestamp.toFixed(1) : null
      return {
        id: `frame-${frame.frame_index ?? index}-${safeTimestamp ?? index}`,
        url: toMediaUrl(String(frame.frame_path)),
        name: safeTimestamp ? `关键帧 ${index + 1} · ${safeTimestamp}s` : `关键帧 ${index + 1}`,
      }
    })
}

function buildReplicationAnalysisReport(replicationOutput: Record<string, any> | null): string {
  const rawReport = replicationOutput?.analysis_report
  if (typeof rawReport === 'string' && rawReport.trim()) return rawReport.trim()

  const replicationPlan = replicationOutput?.replication_plan
  if (!replicationPlan || typeof replicationPlan !== 'object') return ''

  const sections: string[] = ['已完成上传视频解析，以下是本次参考视频的拆解报告。']

  if (replicationPlan.video_summary) {
    sections.push('', '内容概述', String(replicationPlan.video_summary))
  }

  const styleLines = [
    replicationPlan.overall_style ? `整体风格：${replicationPlan.overall_style}` : null,
    replicationPlan.color_palette ? `色彩基调：${replicationPlan.color_palette}` : null,
    replicationPlan.pacing ? `节奏特征：${replicationPlan.pacing}` : null,
  ].filter(Boolean) as string[]
  if (styleLines.length > 0) {
    sections.push('', '风格与节奏', ...styleLines)
  }

  if (replicationOutput?.background_context) {
    sections.push('', '背景信息约束', String(replicationOutput.background_context))
  }

  const audioDesign = replicationPlan.audio_design || {}
  const audioLines = [
    audioDesign.voice_style ? `音色方向：${audioDesign.voice_style}` : null,
    audioDesign.voice_speed !== undefined && audioDesign.voice_speed !== null ? `语速建议：${audioDesign.voice_speed}` : null,
    audioDesign.voice_tone ? `语气风格：${audioDesign.voice_tone}` : null,
    audioDesign.narration_notes ? `口播备注：${audioDesign.narration_notes}` : null,
  ].filter(Boolean) as string[]
  if (audioLines.length > 0) {
    sections.push('', '音频设计', ...audioLines)
  }

  const musicDesign = replicationPlan.music_design || {}
  const musicLines = [
    musicDesign.bgm_mood ? `音乐情绪：${musicDesign.bgm_mood}` : null,
    musicDesign.bgm_style ? `音乐风格：${musicDesign.bgm_style}` : null,
    musicDesign.volume_level ? `音量建议：${musicDesign.volume_level}` : null,
    musicDesign.music_notes ? `音乐备注：${musicDesign.music_notes}` : null,
  ].filter(Boolean) as string[]
  if (musicLines.length > 0) {
    sections.push('', '音乐设计', ...musicLines)
  }

  const shots = Array.isArray(replicationPlan.shots) ? replicationPlan.shots : []
  if (shots.length > 0) {
    sections.push('', '镜头拆解')
    shots.forEach((shot: Record<string, any>, index: number) => {
      sections.push(`镜头 ${(shot.shot_idx ?? index) + 1}：${shot.description || '未提供描述'}`)
      if (shot.visual_design) sections.push(`画面设计：${shot.visual_design}`)
      if (shot.camera_movement) sections.push(`运镜：${shot.camera_movement}`)
      if (shot.color_tone) sections.push(`色调：${shot.color_tone}`)
      if (Array.isArray(shot.subjects) && shot.subjects.length > 0) {
        sections.push(`主体：${shot.subjects.join('、')}`)
      }
      if (Array.isArray(shot.timestamp_range) && shot.timestamp_range.length >= 2) {
        sections.push(`参考时间：${shot.timestamp_range[0]}s - ${shot.timestamp_range[1]}s`)
      }
      if (shot.suggested_duration_seconds !== undefined && shot.suggested_duration_seconds !== null) {
        sections.push(`建议时长：${shot.suggested_duration_seconds}s`)
      }
    })
  }

  return sections.join('\n').trim()
}

function getReplicationShotPreviewUrl(shot: Record<string, any>): string | null {
  if (typeof shot?.material_thumbnail_url === 'string' && shot.material_thumbnail_url) {
    return shot.material_thumbnail_url
  }
  if (typeof shot?.reference_frame_path === 'string' && shot.reference_frame_path) {
    return toMediaUrl(String(shot.reference_frame_path))
  }
  return null
}

function getReplicationShotPreviewName(shot: Record<string, any>, shotNumber: number): string {
  if (typeof shot?.material_filename === 'string' && shot.material_filename) {
    return shot.material_filename
  }
  return `镜头 ${shotNumber}`
}

function getReplicationShotPreviewBadge(shot: Record<string, any>): string | null {
  if (typeof shot?.material_thumbnail_url === 'string' && shot.material_thumbnail_url) {
    return '素材图'
  }
  if (typeof shot?.reference_frame_path === 'string' && shot.reference_frame_path) {
    return '关键帧'
  }
  return null
}

function formatReplicationShotDuration(value: unknown): string | null {
  const duration = Number(value)
  if (!Number.isFinite(duration)) return null
  return `建议 ${duration}s`
}

function formatReplicationTimestampRange(value: unknown): string | null {
  if (!Array.isArray(value) || value.length === 0) return null
  const numbers = value
    .map((item) => Number(item))
    .filter((item) => Number.isFinite(item))
  if (numbers.length === 0) return null
  if (numbers.length === 1) return `${numbers[0]}s`
  return `${numbers[0]}s - ${numbers[1]}s`
}

function hasReplicationDesignDetails(value: unknown): value is Record<string, any> {
  if (!value || typeof value !== 'object') return false
  const entries = Object.entries(value as Record<string, unknown>).filter(([, item]) => item !== null && item !== undefined && item !== '')
  if (entries.length === 0) return false
  if (entries.length === 1 && entries[0][0] === 'voice_speed') return false
  return true
}

function sameMessageImages(
  left?: Array<{ id: string; url: string; name: string }>,
  right?: Array<{ id: string; url: string; name: string }>,
): boolean {
  const lhs = left || []
  const rhs = right || []
  if (lhs.length !== rhs.length) return false
  return lhs.every((item, index) => (
    item.id === rhs[index]?.id
    && item.url === rhs[index]?.url
    && item.name === rhs[index]?.name
  ))
}

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <button
      onClick={handleCopy}
      className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs bg-slate-100 text-slate-600 hover:bg-slate-200 transition-colors"
    >
      {copied ? <Check size={12} className="text-emerald-500" /> : <ClipboardCopy size={12} />}
      {label || (copied ? '已复制' : '复制')}
    </button>
  )
}

function DownloadButton({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      download
      className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs bg-blue-50 text-blue-600 hover:bg-blue-100 transition-colors"
    >
      <Download size={12} /> {label}
    </a>
  )
}

/* ── Per-agent detail renderers ── */

function OrchestratorDetail({ data }: { data: Record<string, unknown> }) {
  const shots = (data.shots || []) as Record<string, unknown>[]
  const script = data.script as string | undefined
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-3 text-xs">
        <div className="rounded-xl bg-slate-50 px-3 py-2">
          <div className="text-slate-400">视频类型</div>
          <div className="text-slate-800 font-medium mt-0.5">{(data.video_type as string) || '-'}</div>
        </div>
        <div className="rounded-xl bg-slate-50 px-3 py-2">
          <div className="text-slate-400">视觉风格</div>
          <div className="text-slate-800 font-medium mt-0.5">{(data.style as string) || '-'}</div>
        </div>
        <div className="rounded-xl bg-slate-50 px-3 py-2">
          <div className="text-slate-400">目标时长</div>
          <div className="text-slate-800 font-medium mt-0.5">{(data.duration_seconds as number) || '-'}s</div>
        </div>
      </div>
      {shots.length > 0 && (
        <div>
          <div className="text-xs text-slate-400 mb-2">分镜规划（{shots.length} 个镜头）</div>
          <div className="space-y-2">
            {shots.map((shot, i) => (
              <div key={i} className="rounded-xl border border-slate-100 bg-white px-3 py-2 text-xs flex items-start gap-3">
                <span className="shrink-0 w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-slate-500 font-medium">{(shot.shot_idx as number) ?? i + 1}</span>
                <div className="flex-1 min-w-0">
                  <div className="text-slate-700">{shot.script_segment as string}</div>
                  <div className="text-slate-400 mt-1">{shot.duration_seconds as number}s</div>
                </div>
                <CopyButton text={shot.script_segment as string} />
              </div>
            ))}
          </div>
        </div>
      )}
      {script && (
        <div className="flex items-center gap-2">
          <CopyButton text={script} label="复制完整脚本" />
        </div>
      )}
    </div>
  )
}

function PromptEngineerDetail({ data }: { data: Record<string, unknown> }) {
  const prompts = (data.shot_prompts || []) as Record<string, unknown>[]
  const voice = (data.voice_params || {}) as Record<string, unknown>
  const voiceId = voice.voice_id as string | undefined
  const voiceSpeed = voice.speed as number | undefined
  const voiceTone = voice.tone as string | undefined
  return (
    <div className="space-y-3">
      {voiceId && (
        <div className="rounded-xl bg-violet-50 border border-violet-100 px-3 py-2 text-xs flex items-center gap-3">
          <Volume2 size={14} className="text-violet-500 shrink-0" />
          <div>
            <span className="text-violet-800 font-medium">语音：{voiceId}</span>
            <span className="text-violet-500 ml-3">语速 {voiceSpeed}x · {voiceTone}</span>
          </div>
        </div>
      )}
      {prompts.length > 0 && (
        <div>
          <div className="text-xs text-slate-400 mb-2">镜头提示词</div>
          <div className="space-y-2">
            {prompts.map((p, i) => {
              const scriptSegment = p.script_segment as string | undefined
              return (
              <div key={i} className="rounded-xl border border-slate-100 bg-white px-3 py-2.5 text-xs">
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <span className="font-medium text-slate-700">镜头 {(p.shot_idx as number) ?? i + 1}</span>
                  <CopyButton text={p.video_prompt as string} label="复制提示词" />
                </div>
                <div className="text-slate-600 leading-5">{p.video_prompt as string}</div>
                {scriptSegment && (
                  <div className="text-slate-400 mt-1.5 italic">旁白：{scriptSegment}</div>
                )}
              </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function AudioSubtitleDetail({ data }: { data: Record<string, unknown> }) {
  const audioPath = data.audio_path as string | undefined
  const subtitlePath = data.subtitle_path as string | undefined
  const durationMs = data.duration_ms as number | undefined
  return (
    <div className="space-y-3">
      {durationMs != null && (
        <div className="text-xs text-slate-500">音频时长：{(durationMs / 1000).toFixed(1)}s</div>
      )}
      <div className="flex flex-wrap gap-2">
        {audioPath && (
          <>
            <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 flex-1 min-w-[200px]">
              <audio src={toMediaUrl(audioPath)} controls className="w-full h-8" />
            </div>
            <DownloadButton href={toMediaUrl(audioPath)} label="下载音频" />
          </>
        )}
        {subtitlePath && (
          <DownloadButton href={toMediaUrl(subtitlePath)} label="下载字幕 (SRT)" />
        )}
      </div>
    </div>
  )
}

function VideoGeneratorDetail({ data }: { data: Record<string, unknown> }) {
  const clips = (data.video_clips || []) as Record<string, unknown>[]
  return (
    <div className="space-y-3">
      <div className="text-xs text-slate-400">已生成 {clips.length} 个镜头片段</div>
      <div className="grid grid-cols-2 gap-3">
        {clips.map((clip, i) => {
          const videoPath = clip.video_path as string | undefined
          const url = videoPath ? toMediaUrl(videoPath) : (clip.video_url as string | undefined)
          return (
            <div key={i} className="rounded-xl border border-slate-200 overflow-hidden bg-white">
              {url ? (
                <video src={url} controls className="w-full aspect-video bg-black" />
              ) : (
                <div className="w-full aspect-video bg-slate-100 flex items-center justify-center text-slate-400">
                  <Play size={24} />
                </div>
              )}
              <div className="px-3 py-2 flex items-center justify-between">
                <span className="text-xs text-slate-600">镜头 {(clip.shot_idx as number) ?? i + 1} · {clip.duration_seconds as number}s</span>
                {url && <DownloadButton href={url} label="下载" />}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function VideoEditorDetail({ data }: { data: Record<string, unknown> }) {
  const videoPath = data.final_video_path as string | undefined
  const durationMs = data.duration_ms as number | undefined
  return (
    <div className="space-y-3">
      {durationMs != null && (
        <div className="text-xs text-slate-500">成片时长：{(durationMs / 1000).toFixed(1)}s</div>
      )}
      {videoPath && (
        <>
          <div className="rounded-xl border border-slate-200 overflow-hidden bg-black">
            <video src={toMediaUrl(videoPath)} controls className="w-full max-h-[320px]" />
          </div>
          <DownloadButton href={toMediaUrl(videoPath)} label="下载成片" />
        </>
      )}
    </div>
  )
}

const AGENT_DETAIL_RENDERERS: Record<string, ComponentType<{ data: Record<string, unknown> }>> = {
  orchestrator: OrchestratorDetail,
  prompt_engineer: PromptEngineerDetail,
  audio_subtitle: AudioSubtitleDetail,
  video_generator: VideoGeneratorDetail,
  video_editor: VideoEditorDetail,
}

function PipelineNodeBoard({
  projectId,
  runId,
  runStatus,
  currentExecution,
  completedExecutions,
  finalVideoPath,
  deliveryInfo,
  onDeliveryInfoChange,
  onConnectDouyin,
  connectingDouyin,
  draftingPublish,
  onRetry,
}: {
  projectId: string
  runId: string
  runStatus: string
  currentExecution: AgentExecution | null
  completedExecutions: AgentExecution[]
  finalVideoPath?: string | null
  deliveryInfo: PipelineDeliveryInfo | null
  onDeliveryInfoChange: (info: PipelineDeliveryInfo | null) => void
  onConnectDouyin: () => void
  connectingDouyin: boolean
  draftingPublish: boolean
  onRetry?: () => void
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()
  const connectedAccounts = deliveryInfo?.connected_social_accounts || []
  const latestPublishDraft = deliveryInfo?.latest_publish_draft || null

  const refreshDeliveryInfo = async () => {
    try {
      const next = await getPipelineDelivery(projectId, runId)
      onDeliveryInfoChange(next)
    } catch {}
  }

  return (
    <div className="max-w-5xl rounded-[28px] border border-slate-200 bg-white/95 shadow-sm p-5 space-y-6">
      <div>
        <div className="text-xs uppercase tracking-[0.18em] text-slate-400">Agent 流程可视化</div>
        <div className="text-sm text-slate-600 mt-2">
          {runStatus === 'completed' ? '所有节点已完成，点击已完成节点查看其输出详情。' :
           runStatus === 'failed' ? '流程在某个节点失败，点击已完成节点查看其输出。' :
           runStatus === 'cancelled' ? '流程已取消。' :
           '当前正在按节点顺序执行，已完成的节点可以点击查看详情。'}
        </div>
      </div>

      <div className="space-y-4">
        {/* Running node */}
        {currentExecution ? (
          <div className="rounded-2xl border border-slate-200 px-4 py-4 bg-slate-50/80">
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-sm font-semibold text-slate-900">{AGENT_LABELS[currentExecution.agent_name]}</div>
                <div className="text-xs text-slate-500 mt-1">{formatExecutionMessage(currentExecution)}</div>
              </div>
              <div className={cn(
                'rounded-full px-3 py-1 text-xs font-medium shrink-0',
                currentExecution.status === 'running' && 'bg-blue-100 text-blue-700',
                currentExecution.status === 'failed' && 'bg-red-100 text-red-700',
                currentExecution.status === 'pending' && 'bg-slate-200 text-slate-600',
              )}>
                {statusText(currentExecution.status)}
              </div>
            </div>
            <div className="mt-3 h-2 rounded-full bg-slate-200 overflow-hidden">
              <div className={cn(
                'h-full rounded-full transition-all',
                currentExecution.status === 'running' && 'bg-blue-500 w-2/3 animate-pulse',
                currentExecution.status === 'failed' && 'bg-red-500 w-full',
                currentExecution.status === 'pending' && 'bg-slate-300 w-1/4',
              )} />
            </div>
            {currentExecution.status === 'failed' && onRetry && (
              <button
                onClick={async () => {
                  setRetrying(true)
                  try { await Promise.resolve(onRetry()) } finally { setRetrying(false) }
                }}
                disabled={retrying}
                className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-orange-500 px-4 py-1.5 text-xs font-medium text-white hover:bg-orange-600 disabled:opacity-50"
              >
                <RotateCcw size={13} className={retrying ? 'animate-spin' : ''} />
                {retrying ? '重试中…' : '重试该节点'}
              </button>
            )}
          </div>
        ) : runStatus === 'completed' || runStatus === 'cancelled' || runStatus === 'failed' ? (
          <div className={cn(
            'rounded-2xl px-4 py-4 text-sm',
            runStatus === 'completed' && 'border border-emerald-200 bg-emerald-50/80 text-emerald-700',
            runStatus === 'failed' && 'border border-red-200 bg-red-50/80 text-red-700',
            runStatus === 'cancelled' && 'border border-slate-200 bg-slate-50/80 text-slate-600',
          )}>
            {runStatus === 'completed' ? '所有节点已执行完成。' : runStatus === 'failed' ? '流程执行失败。' : '流程已取消。'}
          </div>
        ) : null}

        {/* Completed nodes — clickable */}
        {completedExecutions.length > 0 && (
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-400 mb-3">已完成节点（点击展开详情）</div>
            <div className="space-y-2">
              {completedExecutions.map((execution) => {
                const isExpanded = expandedId === execution.id
                const DetailRenderer = AGENT_DETAIL_RENDERERS[execution.agent_name]
                return (
                  <div key={execution.id} className="rounded-2xl border border-slate-200 overflow-hidden transition-all">
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : execution.id)}
                      className="w-full px-4 py-3 flex items-center justify-between gap-3 hover:bg-slate-50/80 transition-colors"
                    >
                      <div className="flex items-center gap-2.5">
                        <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 shrink-0" />
                        <span className="text-sm font-medium text-slate-800">{AGENT_LABELS[execution.agent_name]}</span>
                        <span className="text-xs text-slate-400">{formatExecutionMessage(execution).slice(0, 50)}…</span>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {execution.duration_ms != null && (
                          <span className="text-xs text-slate-400">{(execution.duration_ms / 1000).toFixed(1)}s</span>
                        )}
                        {isExpanded ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                      </div>
                    </button>
                    {isExpanded && (
                      <div className="px-4 pb-4 border-t border-slate-100 pt-3">
                        {DetailRenderer ? (
                          <DetailRenderer data={(execution.output_data || {}) as Record<string, unknown>} />
                        ) : (
                          <pre className="text-xs text-slate-600 bg-slate-50 rounded-xl p-3 overflow-x-auto">
                            {JSON.stringify(execution.output_data, null, 2)}
                          </pre>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* Final video */}
      {runStatus === 'completed' && finalVideoPath && (
        <div className="space-y-3">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-400">成片预览</div>
          <div className="rounded-2xl border border-slate-200 overflow-hidden bg-black">
            <video
              src={toMediaUrl(finalVideoPath)}
              controls
              className="w-full max-h-[480px]"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <DownloadButton href={toMediaUrl(finalVideoPath)} label="下载成片" />
            <button
              onClick={async () => {
                setSaving(true)
                try {
                  await savePipelineVideo(projectId, runId)
                  await refreshDeliveryInfo()
                  toast('success', '视频已保存到仓库')
                } catch (error: any) {
                  toast('error', error?.userMessage || '保存到仓库失败')
                } finally {
                  setSaving(false)
                }
              }}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-colors disabled:opacity-50"
            >
              <FolderUp size={12} />
              {saving ? '保存中…' : '保存到视频仓库'}
            </button>
            {connectedAccounts.length === 0 ? (
              <button
                onClick={onConnectDouyin}
                disabled={connectingDouyin}
                className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs bg-slate-900 text-white hover:bg-slate-800 transition-colors disabled:opacity-50"
              >
                <Send size={12} />
                {connectingDouyin ? '连接中…' : '连接抖音账号'}
              </button>
            ) : latestPublishDraft ? (
              <div className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs bg-slate-100 text-slate-600">
                <Send size={12} />
                {latestPublishDraft.status === 'submitted' ? '抖音发布已提交' : '已生成发布草稿，请在上方确认'}
              </div>
            ) : draftingPublish ? (
              <div className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs bg-slate-100 text-slate-600">
                <Loader2 size={12} className="animate-spin" />
                正在生成抖音发布草稿…
              </div>
            ) : null}
          </div>

          <PlatformPreviewSection
            finalVideoPath={finalVideoPath}
            deliveryInfo={deliveryInfo}
          />
        </div>
      )}
    </div>
  )
}

function PlatformPreviewSection({
  finalVideoPath,
  deliveryInfo,
}: {
  finalVideoPath: string
  deliveryInfo: PipelineDeliveryInfo | null
}) {
  const cards = deliveryInfo?.previews || []
  const records = deliveryInfo?.records || []
  const repositoryRecord = records.find((item) => item.platform === 'repository' && item.action_type === 'save')
  const douyinRecord = records.find((item) => item.platform === 'douyin' && item.action_type === 'publish')

  return (
    <div className="space-y-4">
      <div className="text-xs uppercase tracking-[0.18em] text-slate-400">平台卡片预览</div>
      <div className="grid gap-4 lg:grid-cols-2">
        {cards.map((card) => (
          <div key={card.platform} className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-slate-900">{card.label}</div>
                <div className="mt-1 text-xs text-slate-500">{card.recommended_resolution} · {card.aspect_ratio}</div>
              </div>
              <div className={cn(
                'rounded-full px-2.5 py-1 text-[11px] font-medium',
                card.platform === 'douyin' ? 'bg-slate-900 text-white' : 'bg-red-50 text-red-600',
              )}>
                {card.platform === 'douyin' ? '抖音' : 'YouTube'}
              </div>
            </div>

            <div className={cn(
              'mt-4 overflow-hidden rounded-[24px] border border-slate-200 bg-black',
              card.platform === 'douyin' ? 'mx-auto max-w-[260px]' : '',
            )}>
              <div className={cn(
                card.platform === 'douyin' ? 'aspect-[9/16]' : 'aspect-video',
                'relative bg-black',
              )}>
                <video
                  src={toMediaUrl(finalVideoPath)}
                  className="h-full w-full object-contain"
                  muted
                  playsInline
                  controls
                />
                <div className="pointer-events-none absolute inset-x-0 top-0 bg-gradient-to-b from-black/70 to-transparent px-4 py-3">
                  <div className="text-xs font-semibold tracking-wide text-white/90">{card.cover_title}</div>
                  <div className="mt-1 text-[11px] text-white/75">{card.caption}</div>
                </div>
              </div>
            </div>

            <div className="mt-4 space-y-2 text-xs text-slate-600">
              <div className="font-medium text-slate-800">{card.headline}</div>
              <div>{card.layout_hint}</div>
              <div>{card.safe_zone_tip}</div>
              <div className="text-slate-500">{card.context_hint}</div>
            </div>
          </div>
        ))}
      </div>

      {(repositoryRecord || douyinRecord) && (
        <div className="rounded-2xl border border-slate-200 bg-white px-4 py-4">
          <div className="text-sm font-semibold text-slate-900">交付记录</div>
          <div className="mt-3 space-y-2">
            {repositoryRecord && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-3 text-xs">
                <div>
                  <div className="font-medium text-slate-800">已保存到视频仓库</div>
                  <div className="mt-1 text-slate-500">{new Date(repositoryRecord.created_at).toLocaleString()}</div>
                </div>
                {repositoryRecord.saved_video_path && (
                  <DownloadButton href={toMediaUrl(repositoryRecord.saved_video_path)} label="下载仓库版本" />
                )}
              </div>
            )}
            {douyinRecord && (
              <div className="rounded-xl bg-slate-50 px-3 py-3 text-xs">
                <div className="font-medium text-slate-800">抖音发布结果</div>
                <div className="mt-1 text-slate-600">
                  状态：{douyinRecord.status === 'submitted' || douyinRecord.status === 'published' ? '已提交发布' : douyinRecord.status}
                </div>
                {douyinRecord.draft_payload?.account_name && (
                  <div className="mt-1 text-slate-500">发布账号：{douyinRecord.draft_payload.account_name}</div>
                )}
                {douyinRecord.external_id && (
                  <div className="mt-1 text-slate-500">抖音返回 ID：{douyinRecord.external_id}</div>
                )}
                {douyinRecord.error_message && (
                  <div className="mt-1 text-red-500">{douyinRecord.error_message}</div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function PublishDraftCard({
  draft,
  accounts,
  selectedAccountId,
  connecting,
  publishing,
  onSelectAccount,
  onConnectDouyin,
  onPublish,
}: {
  draft: PublishDraft
  accounts: SocialAccount[]
  selectedAccountId: string | null
  connecting: boolean
  publishing: boolean
  onSelectAccount: (accountId: string | null) => void
  onConnectDouyin: () => void
  onPublish: (payload: {
    social_account_id: string
    title: string
    description: string
    hashtags: string[]
    visibility: string
    cover_title?: string | null
  }) => void
}) {
  const [title, setTitle] = useState(draft.title)
  const [description, setDescription] = useState(draft.description)
  const [hashtagsText, setHashtagsText] = useState(draft.hashtags.join(' '))
  const [coverTitle, setCoverTitle] = useState(draft.cover_title || '')

  useEffect(() => {
    setTitle(draft.title)
    setDescription(draft.description)
    setHashtagsText(draft.hashtags.join(' '))
    setCoverTitle(draft.cover_title || '')
  }, [draft])

  const chosenAccountId = selectedAccountId || draft.social_account_id || accounts[0]?.id || null

  return (
    <div className="mt-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4 text-slate-700">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-400">抖音发布草稿</div>
          <div className="mt-1 text-sm font-medium text-slate-900">{draft.topic || draft.title}</div>
        </div>
        <div className="rounded-full bg-slate-900 px-2.5 py-1 text-[11px] font-medium text-white">
          {draft.status === 'submitted' ? '已提交' : '待确认'}
        </div>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label className="text-xs text-slate-500">
          发布账号
          {accounts.length > 0 ? (
            <select
              value={chosenAccountId || ''}
              onChange={(event) => onSelectAccount(event.target.value || null)}
              className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none"
            >
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.display_name || `抖音账号 ${account.open_id.slice(-6)}`}{account.is_default ? ' · 默认' : ''}
                </option>
              ))}
            </select>
          ) : (
            <button
              onClick={onConnectDouyin}
              disabled={connecting}
              className="mt-1 inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-50"
            >
              {connecting ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
              {connecting ? '连接中…' : '连接抖音账号'}
            </button>
          )}
        </label>
        <label className="text-xs text-slate-500">
          封面标题
          <input
            value={coverTitle}
            onChange={(event) => setCoverTitle(event.target.value)}
            className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none"
          />
        </label>
        <label className="text-xs text-slate-500 md:col-span-2">
          标题
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none"
          />
        </label>
        <label className="text-xs text-slate-500 md:col-span-2">
          文案
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={4}
            className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none"
          />
        </label>
        <label className="text-xs text-slate-500 md:col-span-2">
          话题标签
          <input
            value={hashtagsText}
            onChange={(event) => setHashtagsText(event.target.value)}
            className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none"
            placeholder="#短视频 #AI创作"
          />
        </label>
      </div>
      {draft.risk_tip && <div className="mt-3 text-xs leading-5 text-slate-400">{draft.risk_tip}</div>}
      <div className="mt-4 flex items-center gap-2">
        <button
          onClick={() => {
            if (!chosenAccountId) return
            onPublish({
              social_account_id: chosenAccountId,
              title,
              description,
              hashtags: hashtagsText.split(/\s+/).map((item) => item.trim()).filter(Boolean),
              visibility: draft.visibility || 'public',
              cover_title: coverTitle || null,
            })
          }}
          disabled={publishing || !chosenAccountId}
          className="inline-flex items-center gap-2 rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {publishing ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          {publishing ? '提交中…' : draft.status === 'submitted' ? '重新提交' : '确认发布到抖音'}
        </button>
      </div>
    </div>
  )
}

function statusText(status: string) {
  if (status === 'completed') return '已完成'
  if (status === 'running') return '执行中'
  if (status === 'failed') return '失败'
  return '等待中'
}

function formatExecutionMessage(execution: AgentExecution) {
  if (execution.status === 'failed') {
    return execution.error_message || '该节点执行失败。'
  }
  if (execution.status === 'running' && execution.progress_text) {
    return execution.progress_text
  }

  const output = execution.output_data || {}
  switch (execution.agent_name) {
    case 'orchestrator':
      if (execution.status === 'running') return '正在理解你的脚本和图片，并拆解成可执行的分镜计划。'
      return `已完成需求拆解，共规划 ${output.shots?.length || 0} 个镜头，视频方向为 ${output.video_type || '营销视频'}，接下来会继续生成每个镜头的画面描述和语音风格。`
    case 'prompt_engineer':
      if (execution.status === 'running') return '正在为每个镜头编写可生成的视频描述，并同步设计口播语气。'
      return `已生成 ${output.shot_prompts?.length || 0} 条镜头提示词，并确定本次口播风格为 ${output.voice_params?.tone || '自然说明'}。`
    case 'audio_subtitle':
      if (execution.status === 'running') return '正在合成口播音频并对齐字幕时间轴。'
      return '口播音频与字幕时间轴已经生成完成，后续会和视频片段一起进入剪辑节点。'
    case 'video_generator':
      if (execution.status === 'running') return '正在逐个镜头生成短视频片段，这一步通常耗时最长。'
      return `已完成 ${output.video_clips?.length || 0} 个镜头片段生成，接下来会按字幕节奏进入自动剪辑。`
    case 'video_editor':
      if (execution.status === 'running') return '正在根据字幕节奏重排镜头、拼接音频并生成成片。'
      return '成片已经合成完成，可以直接预览和下载。'
    default:
      return '该节点已产出阶段性结果。'
  }
}
