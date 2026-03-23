import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ComponentType } from 'react'
import { usePipelineStore } from '../../stores/pipelineStore'
import {
  getCategories,
  getMaterials,
  getSelectedMaterials,
  selectMaterial,
  deselectMaterial,
  uploadProjectMaterials,
} from '../../api/materials'
import {
  cancelPipeline,
  generateScript,
  getPipelineAgents,
  getPipelineRun,
  getPipelineUsage,
  launchPipeline,
  preflightCheck,
  retryFailedAgent,
} from '../../api/pipeline'
import type { PreflightCheckResult } from '../../api/pipeline'
import type {
  AgentExecution,
  MaterialCategory,
  MaterialItem,
  MaterialSelection,
} from '../../types'
import { cn } from '../../lib/utils'
import {
  AlertTriangle, Check, ChevronDown, ChevronUp, ClipboardCopy, Download,
  FolderUp, ImagePlus, Loader2, MessageSquareText, Play, RotateCcw, Send,
  Sparkles, StopCircle, Volume2, Wand2, X,
} from 'lucide-react'

const SUPPORTED_EXTS = new Set([
  '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.gif', '.heic', '.heif', '.svg', '.ico', '.avif',
  '.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v', '.3gp',
])

const AGENT_LABELS: Record<string, string> = {
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
  images?: { id: string; url: string; name: string }[]
}

function isSupportedFile(name: string) {
  const dot = name.lastIndexOf('.')
  if (dot === -1) return false
  return SUPPORTED_EXTS.has(name.slice(dot).toLowerCase())
}

interface Props {
  projectId: string
  onSwitchToManual: () => void
}

export default function AutoModeStudio({ projectId, onSwitchToManual }: Props) {
  const {
    currentRun,
    setCurrentRun,
    agentExecutions,
    setAgentExecutions,
    usageSummary,
    setUsageSummary,
  } = usePipelineStore()

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'intro',
      role: 'assistant',
      title: 'Agent 工作台',
      content: '把素材和脚本都放在这里就可以直接一键生成。你可以从左侧素材栏选择图片，也可以在输入框旁直接上传图片。发送后我会在这个对话窗口里持续汇报每个 Agent 的输出。',
    },
  ])
  const [categories, setCategories] = useState<MaterialCategory[]>([])
  const [activeCategory, setActiveCategory] = useState('')
  const [materials, setMaterials] = useState<MaterialItem[]>([])
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [selections, setSelections] = useState<MaterialSelection[]>([])
  const [script, setScript] = useState('')
  const [videoPlatform, setVideoPlatform] = useState('generic')
  const [videoNoAudio, setVideoNoAudio] = useState(true)
  const [preflightWarning, setPreflightWarning] = useState<PreflightCheckResult | null>(null)
  const [launching, setLaunching] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [generatingScript, setGeneratingScript] = useState(false)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const chatUploadRef = useRef<HTMLInputElement>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refreshCategories = useCallback(async () => {
    const cats = await getCategories()
    setCategories(cats)
    setActiveCategory((prev) => prev || cats[0]?.name || '')
  }, [])

  const refreshSelections = useCallback(async () => {
    const sels = await getSelectedMaterials(projectId)
    setSelections(sels)
    setSelectedIds(new Set(sels.map((s) => s.material_id)))
  }, [projectId])

  const refreshMaterials = useCallback(async (category: string) => {
    if (!category) return
    const result = await getMaterials(category, 1, 100)
    setMaterials(result.items)
  }, [])

  useEffect(() => {
    refreshCategories().catch(() => {})
    refreshSelections().catch(() => {})
  }, [refreshCategories, refreshSelections])

  useEffect(() => {
    if (!activeCategory) return
    refreshMaterials(activeCategory).catch(() => {})
  }, [activeCategory, refreshMaterials])

  useEffect(() => {
    if (!currentRun) return

    const poll = async () => {
      try {
        const run = await getPipelineRun(projectId, currentRun.id)
        setCurrentRun(run)

        const executions = await getPipelineAgents(projectId, currentRun.id)
        setAgentExecutions(executions)

        try {
          const usage = await getPipelineUsage(projectId, currentRun.id)
          setUsageSummary(usage)
        } catch {}

        if (run.status === 'completed' || run.status === 'failed' || run.status === 'cancelled') {
          if (run.status === 'completed') {
            // Clear selections only on success — keep them on failure so user can retry
            setSelections([])
            setSelectedIds(new Set())
          }
          if (pollRef.current) {
            clearInterval(pollRef.current)
            pollRef.current = null
          }
        }
      } catch {}
    }

    poll()
    pollRef.current = setInterval(poll, 2500)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [currentRun?.id, projectId])

  const handleToggleMaterial = async (item: MaterialItem) => {
    if (selectedIds.has(item.id)) {
      await deselectMaterial(projectId, item.id)
      setSelectedIds((prev) => {
        const next = new Set(prev)
        next.delete(item.id)
        return next
      })
      setSelections((prev) => prev.filter((s) => s.material_id !== item.id))
      return
    }

    const selection = await selectMaterial(projectId, item.id, item.category, selections.length)
    setSelectedIds((prev) => new Set(prev).add(item.id))
    setSelections((prev) => [...prev, selection])
  }

  const uploadFiles = async (fileList: FileList | null, kind: 'folder' | 'file' | 'chat') => {
    if (!fileList || fileList.length === 0) return
    const payload: { file: File; relativePath: string }[] = []
    for (let i = 0; i < fileList.length; i++) {
      const file = fileList[i]
      if (!isSupportedFile(file.name)) continue
      const relativePath =
        kind === 'folder'
          ? ((file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name)
          : `对话上传/${file.name}`
      payload.push({ file, relativePath })
    }
    if (payload.length === 0) return

    setUploading(true)
    try {
      const autoSelect = kind === 'chat'
      const result = await uploadProjectMaterials(projectId, payload, autoSelect)
      await refreshCategories()
      await refreshSelections()
      const targetCategory = result.selected_items?.[0]?.category || result.uploaded_items?.[0]?.category
      if (targetCategory) {
        setActiveCategory(targetCategory)
        await refreshMaterials(targetCategory)
      }
      if (kind === 'chat') {
        setMessages((prev) => [
          ...prev,
          {
            id: `upload-${Date.now()}`,
            role: 'assistant',
            title: '素材已就绪',
            content: `已上传并自动选中 ${result.selected_items?.length || 0} 张素材，现在可以直接发送脚本。`,
          },
        ])
      } else {
        setMessages((prev) => [
          ...prev,
          {
            id: `upload-${Date.now()}`,
            role: 'assistant',
            title: '素材已入库',
            content: `已上传 ${result.uploaded_items?.length || result.files || 0} 个素材到左侧栏，当前不会自动全选，你可以手动挑选本次需要参与生成的图片。`,
          },
        ])
      }
    } finally {
      setUploading(false)
    }
  }

  const handleGenerateScript = async () => {
    if (selections.length === 0) return
    setGeneratingScript(true)
    const imageIds = selections.map((s) => s.material_id)
    const msgImages = selectedMaterials.map((m) => ({ id: m.id, url: m.thumbnail_url || '', name: m.filename }))

    setMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        role: 'user',
        title: '请求 AI 生成脚本',
        content: `已选 ${imageIds.length} 张素材，请根据图片内容生成脚本。`,
        images: msgImages,
      },
    ])

    try {
      const result = await generateScript(projectId, imageIds)
      setScript(result.script)
      setMessages((prev) => [
        ...prev,
        {
          id: `script-${Date.now()}`,
          role: 'assistant',
          title: 'AI 脚本建议',
          content: `已根据你的素材生成了以下脚本，已自动填入输入框，你可以修改后发送：\n\n${result.script}`,
        },
      ])
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '未知错误'
      setMessages((prev) => [
        ...prev,
        {
          id: `script-err-${Date.now()}`,
          role: 'assistant',
          title: '脚本生成失败',
          content: `抱歉，AI 脚本生成失败：${msg}`,
        },
      ])
    } finally {
      setGeneratingScript(false)
    }
  }

  const handleSend = async () => {
    const trimmed = script.trim()
    if (!trimmed || selections.length === 0) return

    setLaunching(true)
    setPreflightWarning(null)
    const imageIds = selections.map((s) => s.material_id)
    const msgImages = selectedMaterials.map((m) => ({ id: m.id, url: m.thumbnail_url || '', name: m.filename }))
    const autoDuration = imageIds.length * 5

    // Preflight check: script vs material balance
    try {
      const check = await preflightCheck(projectId, {
        script: trimmed,
        image_count: imageIds.length,
        duration_seconds: autoDuration,
        duration_mode: 'fixed',
      })
      if (!check.ok) {
        setPreflightWarning(check)
        setLaunching(false)
        return
      }
    } catch {
      // preflight failed, proceed anyway
    }

    setMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        role: 'user',
        title: '用户脚本',
        content: trimmed,
        images: msgImages,
      },
      {
        id: `system-${Date.now()}`,
        role: 'assistant',
        title: '调度已开始',
        content: '已收到你的脚本和图片素材，我正在安排调度 Agent 启动整条一键生成流水线。下方会用 Agent 节点和进度条持续展示当前进度，并仅显示适合给用户看的阶段输出。',
      },
    ])

    try {
      const run = await launchPipeline(projectId, {
        script: trimmed,
        image_ids: imageIds,
        platform: videoPlatform,
        duration_seconds: autoDuration,
        duration_mode: 'fixed',
        no_audio: videoNoAudio,
        style: 'commercial',
        voice_id: 'Chelsie',
      })
      setCurrentRun(run)
      setScript('')
      // Keep selections so user can retry with same materials if pipeline fails
    } finally {
      setLaunching(false)
    }
  }

  const selectedMaterials = useMemo(
    () => selections.map((selection) => selection.material).filter((item): item is MaterialItem => Boolean(item)),
    [selections],
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

  const runStatusText = currentRun
    ? currentRun.status === 'completed'
      ? '已完成'
      : currentRun.status === 'failed'
        ? '失败'
        : currentRun.status === 'cancelled'
          ? '已取消'
          : currentRun.current_agent
            ? `执行中：${AGENT_LABELS[currentRun.current_agent] || currentRun.current_agent}`
            : '准备执行'
    : '等待发送'

  return (
    <div className="h-full flex bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.10),_transparent_35%),linear-gradient(180deg,#f8fafc_0%,#eef2ff_100%)]">
      <aside className="w-[320px] border-r border-slate-200 bg-white/80 backdrop-blur shrink-0 flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold text-slate-900">素材侧栏</div>
              <div className="text-xs text-slate-500 mt-1">上传素材库或挑选图片后，直接发脚本即可</div>
            </div>
            <Sparkles size={16} className="text-blue-500" />
          </div>
          <div className="grid grid-cols-2 gap-2 mt-4">
            <button
              onClick={() => folderInputRef.current?.click()}
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 hover:bg-slate-100 flex items-center justify-center gap-2"
            >
              <FolderUp size={14} /> 素材库
            </button>
            <button
              onClick={() => fileInputRef.current?.click()}
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 hover:bg-slate-100 flex items-center justify-center gap-2"
            >
              <ImagePlus size={14} /> 单张图片
            </button>
          </div>
          <input
            ref={folderInputRef}
            type="file"
            className="hidden"
            {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
            multiple
            onChange={(e) => uploadFiles(e.target.files, 'folder')}
          />
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept="image/*,video/*"
            multiple
            onChange={(e) => uploadFiles(e.target.files, 'file')}
          />
        </div>

        <div className="px-4 pt-4 pb-2">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-400">分类</div>
          <div className="flex flex-wrap gap-2 mt-3">
            {categories.map((cat) => (
              <button
                key={cat.name}
                onClick={() => setActiveCategory(cat.name)}
                className={cn(
                  'rounded-full px-3 py-1.5 text-xs transition-colors',
                  activeCategory === cat.name ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200',
                )}
              >
                {cat.name} ({cat.count})
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 pb-4">
          {uploading ? (
            <div className="h-full flex items-center justify-center text-slate-500">
              <Loader2 size={20} className="animate-spin mr-2" /> 正在处理素材...
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              {materials.map((item) => {
                const selected = selectedIds.has(item.id)
                return (
                  <button
                    key={item.id}
                    onClick={() => handleToggleMaterial(item)}
                    className={cn(
                      'rounded-2xl overflow-hidden border text-left bg-white transition-all',
                      selected ? 'border-blue-500 ring-2 ring-blue-200' : 'border-slate-200 hover:border-slate-300',
                    )}
                  >
                    <div className="aspect-[4/3] bg-slate-100">
                      <img src={item.thumbnail_url || ''} alt={item.filename} className="w-full h-full object-cover" />
                    </div>
                    <div className="px-3 py-2">
                      <div className="text-xs font-medium text-slate-800 truncate">{item.filename}</div>
                      <div className="text-[11px] text-slate-500 mt-1">{selected ? '已选中' : '点击选择'}</div>
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </aside>

      <section className="flex-1 flex flex-col">
        <div className="px-6 py-4 border-b border-slate-200 bg-white/70 backdrop-blur">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-lg font-semibold text-slate-900">对话式一键生成</div>
              <div className="text-sm text-slate-500 mt-1">发送后不跳转，Agent 的进程和输出都会持续显示在对话窗口里</div>
            </div>
            <div className="flex items-center gap-2">
              <button className="rounded-full bg-blue-600 text-white px-4 py-2 text-sm font-medium flex items-center gap-2">
                <Wand2 size={14} /> 一键生成
              </button>
              <button
                onClick={onSwitchToManual}
                className="rounded-full bg-white border border-slate-200 text-slate-700 px-4 py-2 text-sm font-medium hover:bg-slate-50"
              >
                手动模式
              </button>
            </div>
          </div>
        </div>

        <div className="px-6 py-4 border-b border-slate-200 bg-white/60 backdrop-blur flex items-center justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-400">当前状态</div>
            <div className="text-sm font-medium text-slate-800 mt-1">{runStatusText}</div>
            {usageSummary && (
              <div className="text-xs text-slate-500 mt-1">
                已统计 Tokens：{usageSummary.total_tokens.toLocaleString()}，模型调用 {usageSummary.request_count} 次
              </div>
            )}
          </div>
          {currentRun && (currentRun.status === 'pending' || currentRun.status === 'running') && (
            <button
              onClick={async () => {
                await cancelPipeline(projectId, currentRun.id)
                const run = await getPipelineRun(projectId, currentRun.id)
                setCurrentRun(run)
              }}
              className="rounded-full px-4 py-2 text-sm bg-red-50 text-red-600 hover:bg-red-100 flex items-center gap-2"
            >
              <StopCircle size={14} /> 取消
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
          {messages.map((message) => (
            <div
              key={message.id}
              className={cn(
                'max-w-4xl rounded-3xl px-5 py-4 shadow-sm whitespace-pre-wrap',
                message.role === 'assistant' && 'bg-white border border-slate-200 text-slate-700',
                message.role === 'user' && 'bg-blue-600 text-white ml-auto',
                message.role === 'system' && 'bg-amber-50 border border-amber-200 text-amber-900',
              )}
            >
              <div className="text-xs uppercase tracking-[0.18em] opacity-60 mb-2">
                {message.title || (message.role === 'user' ? '用户输入' : '系统消息')}
              </div>
              <div className="text-sm leading-6">{message.content}</div>
              {message.images && message.images.length > 0 && (
                <div className="flex gap-2 mt-3 overflow-x-auto">
                  {message.images.map((img) => (
                    <div key={img.id} className="w-16 h-12 shrink-0 rounded-lg overflow-hidden border border-white/30 bg-black/10">
                      <img src={img.url} alt={img.name} className="w-full h-full object-cover" />
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}

          {currentRun && (
            <PipelineNodeBoard
              runStatus={currentRun.status}
              currentExecution={currentExecution}
              completedExecutions={completedExecutions}
              finalVideoPath={currentRun.final_video_path}
              onRetry={async () => {
                try {
                  const updated = await retryFailedAgent(projectId, currentRun.id)
                  setCurrentRun(updated)
                } catch (e: any) {
                  setMessages((prev) => [...prev, {
                    id: `retry-err-${Date.now()}`,
                    role: 'system',
                    content: `重试失败：${e?.response?.data?.detail || e.message}`,
                  }])
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
                      onClick={() => handleToggleMaterial(item)}
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
            <textarea
              value={script}
              onChange={(e) => setScript(e.target.value)}
              rows={5}
              placeholder="输入脚本后点击发送，调度 Agent 会直接读取你当前选中的图片并发起一键生成..."
              className="w-full resize-none outline-none text-sm text-slate-800 placeholder:text-slate-400 bg-transparent"
            />
            <div className="mt-3 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <button
                  onClick={() => chatUploadRef.current?.click()}
                  className="rounded-full px-3 py-2 text-sm text-slate-600 bg-slate-100 hover:bg-slate-200 flex items-center gap-2"
                >
                  <ImagePlus size={14} /> 上传到对话栏
                </button>
                <button
                  onClick={handleGenerateScript}
                  disabled={generatingScript || selections.length === 0}
                  className="rounded-full px-3 py-2 text-sm text-violet-600 bg-violet-50 hover:bg-violet-100 disabled:opacity-50 flex items-center gap-2"
                >
                  {generatingScript ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                  AI 生成脚本
                </button>
                <input
                  ref={chatUploadRef}
                  type="file"
                  className="hidden"
                  accept="image/*,video/*"
                  multiple
                  onChange={(e) => uploadFiles(e.target.files, 'chat')}
                />
                <div className="flex items-center gap-1.5 text-sm text-slate-600">
                  <span className="text-xs text-slate-400">平台</span>
                  <select
                    value={videoPlatform}
                    onChange={(e) => setVideoPlatform(e.target.value)}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm text-slate-700 outline-none"
                  >
                    <option value="generic">通用 16:9</option>
                    <option value="douyin">抖音 9:16</option>
                    <option value="xiaohongshu">小红书 3:4</option>
                    <option value="bilibili">B站 16:9</option>
                  </select>
                </div>
                <div className="flex items-center gap-1.5 text-sm text-slate-500">
                  <span className="text-xs text-slate-400">时长</span>
                  <span className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm text-slate-700">
                    {selections.length > 0 ? `${selections.length * 5}s` : '—'}
                  </span>
                  <span className="text-xs text-slate-400">({selections.length}张×5s)</span>
                </div>
                <label className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={!videoNoAudio}
                    onChange={(e) => setVideoNoAudio(!e.target.checked)}
                    className="rounded border-slate-300"
                  />
                  <Volume2 size={14} className={videoNoAudio ? 'text-slate-300' : 'text-violet-500'} />
                  <span className="text-xs text-slate-400">视频原声</span>
                </label>
              </div>
              <button
                onClick={handleSend}
                disabled={launching || !script.trim() || selections.length === 0 || (currentRun?.status === 'running' || currentRun?.status === 'pending')}
                className="rounded-full px-5 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium disabled:opacity-50 flex items-center gap-2"
              >
                {launching ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                发送并生成
              </button>
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
                  </div>
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() => { setPreflightWarning(null) }}
                      className="rounded-lg bg-amber-100 px-3 py-1 text-xs text-amber-700 hover:bg-amber-200"
                    >
                      我知道了，继续调整
                    </button>
                    <button
                      onClick={() => {
                        setPreflightWarning(null)
                        // Force send bypassing preflight
                        setLaunching(true)
                        const imageIds = selections.map((s) => s.material_id)
                        const msgImages = selectedMaterials.map((m) => ({ id: m.id, url: m.thumbnail_url || '', name: m.filename }))
                        setMessages((prev) => [
                          ...prev,
                          { id: `user-${Date.now()}`, role: 'user', title: '用户脚本', content: script.trim(), images: msgImages },
                          { id: `system-${Date.now()}`, role: 'assistant', title: '调度已开始', content: '已收到你的脚本和图片素材（音频可能超出视频时长，系统会自动调整），正在启动流水线。' },
                        ])
                        launchPipeline(projectId, {
                          script: script.trim(),
                          image_ids: imageIds,
                          platform: videoPlatform,
                          duration_seconds: imageIds.length * 5,
                          duration_mode: 'fixed',
                          no_audio: videoNoAudio,
                          style: 'commercial',
                          voice_id: 'Chelsie',
                        }).then((run) => {
                          setCurrentRun(run)
                          setScript('')
                        }).catch((e) => {
                          setMessages((prev) => [...prev, { id: `err-${Date.now()}`, role: 'assistant', content: `启动失败：${e?.response?.data?.detail || e.message}` }])
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
          </div>
        </div>
      </section>
    </div>
  )
}

const AGENT_ORDER = [
  'orchestrator',
  'prompt_engineer',
  'audio_subtitle',
  'video_generator',
  'video_editor',
]

function toMediaUrl(path: string): string {
  // Remote URLs (from WaveSpeed etc.) are used directly
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  // Local paths like "./data/generated/xxx.mp4" → "/generated/xxx.mp4"
  const filename = path.split('/').pop() || path
  return `/generated/${filename}`
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
  runStatus,
  currentExecution,
  completedExecutions,
  finalVideoPath,
  onRetry,
}: {
  runStatus: string
  currentExecution: AgentExecution | null
  completedExecutions: AgentExecution[]
  finalVideoPath?: string | null
  onRetry?: () => void
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)

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
          <DownloadButton href={toMediaUrl(finalVideoPath)} label="下载成片" />
        </div>
      )}
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
