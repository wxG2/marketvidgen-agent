import { useState, useEffect, useRef } from 'react'
import {
  uploadModelImage,
  getModelImages,
  deleteModelImage,
  uploadAudio,
  createTalkingHeadTask,
  getTalkingHeadTasks,
  triggerComposite,
  updatePromptAndAudio,
  triggerLipSync,
} from '../../api/talkingHead'
import { getCategories, getMaterials } from '../../api/materials'
import type { ModelImage, TalkingHeadTask, MaterialItem, MaterialCategory } from '../../types'
import type { AudioUploadResult } from '../../api/talkingHead'
import {
  Upload,
  Trash2,
  Loader2,
  CheckCircle,
  XCircle,
  Image as ImageIcon,
  Mic,
  Wand2,
  RefreshCw,
  Check,
  ChevronDown,
  Music,
  X,
} from 'lucide-react'
import { cn } from '../../lib/utils'

interface Props {
  projectId: string
  onSelectionChange?: () => void
}

export default function TalkingHeadPanel({ projectId, onSelectionChange }: Props) {
  // Model images
  const [modelImages, setModelImages] = useState<ModelImage[]>([])
  const [selectedModelImage, setSelectedModelImage] = useState<ModelImage | null>(null)
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Background materials
  const [categories, setCategories] = useState<MaterialCategory[]>([])
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)
  const [bgMaterials, setBgMaterials] = useState<MaterialItem[]>([])
  const [selectedBgMaterial, setSelectedBgMaterial] = useState<MaterialItem | null>(null)
  const [loadingMaterials, setLoadingMaterials] = useState(false)

  // Audio
  const [audioFile, setAudioFile] = useState<AudioUploadResult | null>(null)
  const [uploadingAudio, setUploadingAudio] = useState(false)
  const audioInputRef = useRef<HTMLInputElement>(null)

  // Tasks
  const [tasks, setTasks] = useState<TalkingHeadTask[]>([])
  const [polling, setPolling] = useState(false)

  // New task form
  const [motionPrompt, setMotionPrompt] = useState('')

  // Load data
  useEffect(() => {
    getModelImages(projectId).then(setModelImages)
    getTalkingHeadTasks(projectId).then((t) => {
      setTasks(t)
      if (t.some(needsPolling)) setPolling(true)
    })
    getCategories().then(setCategories)
  }, [projectId])

  // Load materials when category changes
  useEffect(() => {
    if (!selectedCategory) {
      setBgMaterials([])
      return
    }
    setLoadingMaterials(true)
    getMaterials(selectedCategory, 1, 100).then((page) => {
      setBgMaterials(page.items)
      setLoadingMaterials(false)
    })
  }, [selectedCategory])

  // Polling
  useEffect(() => {
    if (!polling) return
    const timer = setInterval(async () => {
      const updated = await getTalkingHeadTasks(projectId)
      setTasks(updated)
      if (!updated.some(needsPolling)) {
        setPolling(false)
        onSelectionChange?.()
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [polling, projectId])

  const needsPolling = (t: TalkingHeadTask) =>
    t.composite_status === 'processing' || t.lipsync_status === 'processing'

  // Upload model image
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const img = await uploadModelImage(projectId, file)
      setModelImages((prev) => [img, ...prev])
      setSelectedModelImage(img)
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDeleteImage = async (img: ModelImage) => {
    await deleteModelImage(img.id)
    setModelImages((prev) => prev.filter((m) => m.id !== img.id))
    if (selectedModelImage?.id === img.id) setSelectedModelImage(null)
  }

  // Upload audio
  const handleAudioUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploadingAudio(true)
    try {
      const result = await uploadAudio(projectId, file)
      setAudioFile(result)
    } finally {
      setUploadingAudio(false)
      if (audioInputRef.current) audioInputRef.current.value = ''
    }
  }

  // Create task
  const handleCreateTask = async () => {
    if (!selectedModelImage) return
    const task = await createTalkingHeadTask(projectId, {
      model_image_id: selectedModelImage.id,
      bg_material_id: selectedBgMaterial?.id || null,
      motion_prompt: motionPrompt || null,
      audio_segment_url: audioFile?.file_url || null,
    })
    setTasks((prev) => [...prev, task])
  }

  // Trigger composite
  const handleComposite = async (taskId: string) => {
    const updated = await triggerComposite(taskId)
    setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)))
    setPolling(true)
  }

  // Update prompt
  const handleUpdatePrompt = async (taskId: string, prompt: string, audioUrl: string) => {
    const updated = await updatePromptAndAudio(taskId, {
      motion_prompt: prompt,
      audio_segment_url: audioUrl,
    })
    setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)))
  }

  // Upload audio for existing task
  const handleTaskAudioUpload = async (taskId: string, file: File) => {
    const result = await uploadAudio(projectId, file)
    const updated = await updatePromptAndAudio(taskId, {
      audio_segment_url: result.file_url,
    })
    setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)))
  }

  // Trigger lipsync
  const handleLipSync = async (taskId: string) => {
    const updated = await triggerLipSync(taskId)
    setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)))
    setPolling(true)
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {/* Step A: Model & Background */}
      <section>
        <h3 className="text-sm font-medium text-gray-900 mb-3">Step A: 模特 & 环境</h3>
        <div className="flex gap-6">
          {/* Model image upload */}
          <div className="w-60 shrink-0">
            <p className="text-xs text-gray-500 mb-2">模特图片</p>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              onChange={handleUpload}
              className="hidden"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="w-full h-32 border-2 border-dashed border-gray-300 rounded-lg flex flex-col items-center justify-center text-gray-400 hover:border-blue-400 hover:text-blue-500 transition-colors"
            >
              {uploading ? (
                <Loader2 className="animate-spin" size={24} />
              ) : (
                <>
                  <Upload size={24} />
                  <span className="text-xs mt-1">上传模特照片</span>
                </>
              )}
            </button>

            {/* Uploaded images list */}
            {modelImages.length > 0 && (
              <div className="mt-3 space-y-2">
                {modelImages.map((img) => (
                  <div
                    key={img.id}
                    onClick={() => setSelectedModelImage(img)}
                    className={cn(
                      'flex items-center gap-2 p-2 rounded-lg cursor-pointer border transition-colors',
                      selectedModelImage?.id === img.id
                        ? 'border-blue-500 bg-blue-50'
                        : 'border-gray-200 hover:border-gray-300',
                    )}
                  >
                    <img
                      src={img.file_url}
                      alt={img.filename}
                      className="w-10 h-10 rounded object-cover"
                    />
                    <span className="text-xs text-gray-700 flex-1 truncate">{img.filename}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDeleteImage(img)
                      }}
                      className="text-gray-300 hover:text-red-500"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Background material picker */}
          <div className="flex-1">
            <p className="text-xs text-gray-500 mb-2">环境素材（可选，从素材库选择）</p>

            {/* Category selector */}
            <div className="mb-3">
              <div className="relative">
                <select
                  value={selectedCategory || ''}
                  onChange={(e) => {
                    setSelectedCategory(e.target.value || null)
                    setSelectedBgMaterial(null)
                  }}
                  className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg appearance-none bg-white pr-8"
                >
                  <option value="">选择素材分类...</option>
                  {categories.map((cat) => (
                    <option key={cat.name} value={cat.name}>
                      {cat.name} ({cat.count})
                    </option>
                  ))}
                </select>
                <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
              </div>
            </div>

            {/* Selected material preview */}
            {selectedBgMaterial && (
              <div className="mb-3 flex items-center gap-3 p-2 bg-blue-50 border border-blue-200 rounded-lg">
                {selectedBgMaterial.thumbnail_url ? (
                  <img
                    src={selectedBgMaterial.thumbnail_url}
                    alt={selectedBgMaterial.filename}
                    className="w-16 h-16 rounded object-cover"
                  />
                ) : (
                  <div className="w-16 h-16 rounded bg-gray-100 flex items-center justify-center">
                    <ImageIcon size={20} className="text-gray-300" />
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-gray-900 truncate">{selectedBgMaterial.filename}</p>
                  <p className="text-xs text-blue-600">{selectedBgMaterial.category}</p>
                </div>
                <button
                  onClick={() => setSelectedBgMaterial(null)}
                  className="text-gray-400 hover:text-red-500"
                >
                  <X size={14} />
                </button>
              </div>
            )}

            {/* Materials grid */}
            {selectedCategory && (
              <div className="max-h-48 overflow-y-auto">
                {loadingMaterials ? (
                  <div className="flex items-center justify-center py-8 text-gray-400">
                    <Loader2 className="animate-spin" size={20} />
                    <span className="ml-2 text-xs">加载素材...</span>
                  </div>
                ) : bgMaterials.length === 0 ? (
                  <div className="text-center py-8 text-gray-400 text-xs">该分类下暂无素材</div>
                ) : (
                  <div className="grid grid-cols-4 gap-2">
                    {bgMaterials.map((mat) => (
                      <div
                        key={mat.id}
                        onClick={() => setSelectedBgMaterial(mat)}
                        className={cn(
                          'aspect-square rounded-lg overflow-hidden cursor-pointer border-2 transition-all',
                          selectedBgMaterial?.id === mat.id
                            ? 'border-blue-500 ring-1 ring-blue-500/30'
                            : 'border-transparent hover:border-gray-300',
                        )}
                      >
                        {mat.thumbnail_url ? (
                          <img
                            src={mat.thumbnail_url}
                            alt={mat.filename}
                            className="w-full h-full object-cover"
                          />
                        ) : (
                          <div className="w-full h-full bg-gray-100 flex items-center justify-center">
                            <ImageIcon size={16} className="text-gray-300" />
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {!selectedCategory && !selectedBgMaterial && (
              <div className="h-32 border-2 border-dashed border-gray-200 rounded-lg flex items-center justify-center text-gray-300">
                <div className="text-center">
                  <ImageIcon size={24} className="mx-auto mb-1" />
                  <p className="text-xs">请先选择素材分类</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* Create new task */}
      {selectedModelImage && (
        <section className="bg-gray-50 rounded-xl p-4 space-y-4">
          <div className="flex items-center gap-4">
            {/* Model image preview */}
            <img
              src={selectedModelImage.file_url}
              alt=""
              className="w-14 h-14 rounded-lg object-cover border border-gray-200"
            />
            {/* BG preview */}
            {selectedBgMaterial?.thumbnail_url ? (
              <>
                <span className="text-gray-300 text-lg">+</span>
                <img
                  src={selectedBgMaterial.thumbnail_url}
                  alt=""
                  className="w-14 h-14 rounded-lg object-cover border border-gray-200"
                />
              </>
            ) : null}
            <div className="flex-1 min-w-0">
              <p className="text-sm text-gray-700">
                模特：<span className="font-medium">{selectedModelImage.filename}</span>
                {selectedBgMaterial && (
                  <span className="text-gray-400 ml-2">
                    环境：{selectedBgMaterial.filename}
                  </span>
                )}
              </p>
            </div>
          </div>

          {/* Prompt */}
          <div>
            <label className="text-xs text-gray-500 mb-1 block">口播动作提示词</label>
            <input
              type="text"
              value={motionPrompt}
              onChange={(e) => setMotionPrompt(e.target.value)}
              placeholder="如：微笑介绍，自然手势，偶尔点头"
              className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-lg"
            />
          </div>

          {/* Audio upload */}
          <div>
            <label className="text-xs text-gray-500 mb-1 block">音频片段</label>
            <input
              ref={audioInputRef}
              type="file"
              accept="audio/*"
              onChange={handleAudioUpload}
              className="hidden"
            />
            {audioFile ? (
              <div className="flex items-center gap-3 p-2 bg-white border border-gray-200 rounded-lg">
                <Music size={16} className="text-blue-500 shrink-0" />
                <span className="text-sm text-gray-700 flex-1 truncate">{audioFile.filename}</span>
                <audio src={audioFile.file_url} controls className="h-8 max-w-[200px]" />
                <button
                  onClick={() => setAudioFile(null)}
                  className="text-gray-400 hover:text-red-500 shrink-0"
                >
                  <X size={14} />
                </button>
              </div>
            ) : (
              <button
                onClick={() => audioInputRef.current?.click()}
                disabled={uploadingAudio}
                className="flex items-center gap-2 px-3 py-2 border border-dashed border-gray-300 rounded-lg text-gray-400 hover:border-blue-400 hover:text-blue-500 transition-colors text-sm"
              >
                {uploadingAudio ? (
                  <Loader2 className="animate-spin" size={16} />
                ) : (
                  <Upload size={16} />
                )}
                上传音频文件
              </button>
            )}
          </div>

          {/* Create button */}
          <div className="flex justify-end">
            <button
              onClick={handleCreateTask}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm flex items-center gap-2"
            >
              <Wand2 size={16} />
              创建口播任务
            </button>
          </div>
        </section>
      )}

      {/* Task list */}
      {tasks.length > 0 && (
        <section>
          <h3 className="text-sm font-medium text-gray-900 mb-3">口播任务列表</h3>
          <div className="space-y-4">
            {tasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                projectId={projectId}
                onComposite={handleComposite}
                onUpdatePrompt={handleUpdatePrompt}
                onAudioUpload={handleTaskAudioUpload}
                onLipSync={handleLipSync}
              />
            ))}
          </div>
        </section>
      )}

      {/* Empty state */}
      {tasks.length === 0 && !selectedModelImage && (
        <div className="text-center text-gray-400 mt-12">
          <Mic className="mx-auto mb-4" size={64} />
          <h2 className="text-xl text-gray-900 mb-2">口播视频生成</h2>
          <p>上传模特图片开始创建口播视频</p>
        </div>
      )}
    </div>
  )
}

// ── Task Card Component ───────────────────────────────────────────────

function TaskCard({
  task,
  projectId,
  onComposite,
  onUpdatePrompt,
  onAudioUpload,
  onLipSync,
}: {
  task: TalkingHeadTask
  projectId: string
  onComposite: (id: string) => void
  onUpdatePrompt: (id: string, prompt: string, audioUrl: string) => void
  onAudioUpload: (id: string, file: File) => void
  onLipSync: (id: string) => void
}) {
  const [editPrompt, setEditPrompt] = useState(task.motion_prompt || '')
  const [editAudio, setEditAudio] = useState(task.audio_segment_url || '')
  const audioInputRef = useRef<HTMLInputElement>(null)
  const [uploadingAudio, setUploadingAudio] = useState(false)

  const handleAudioChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploadingAudio(true)
    try {
      await onAudioUpload(task.id, file)
    } finally {
      setUploadingAudio(false)
      if (audioInputRef.current) audioInputRef.current.value = ''
    }
  }

  // Sync from parent when task updates
  useEffect(() => {
    setEditAudio(task.audio_segment_url || '')
  }, [task.audio_segment_url])

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      {/* Step B: Composite */}
      <div className="p-4 border-b border-gray-100">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-gray-500 uppercase">Step B: 图片合成</span>
          <StatusBadge status={task.composite_status} />
        </div>
        <div className="flex items-center gap-4">
          {task.model_image_url && (
            <div className="text-center">
              <img
                src={task.model_image_url}
                alt="模特"
                className="w-20 h-20 rounded-lg object-cover"
              />
              <p className="text-[10px] text-gray-400 mt-1">模特</p>
            </div>
          )}
          {task.bg_thumbnail_url && (
            <>
              <span className="text-gray-300 text-lg">+</span>
              <div className="text-center">
                <img
                  src={task.bg_thumbnail_url}
                  alt="环境"
                  className="w-20 h-20 rounded-lg object-cover"
                />
                <p className="text-[10px] text-gray-400 mt-1">环境</p>
              </div>
            </>
          )}
          {task.composite_preview_url && (
            <>
              <span className="text-gray-300 text-lg">=</span>
              <div className="text-center">
                <img
                  src={task.composite_preview_url}
                  alt="合成预览"
                  className="w-20 h-20 rounded-lg object-cover border-2 border-green-400"
                />
                <p className="text-[10px] text-green-600 mt-1">合成结果</p>
              </div>
            </>
          )}
          <div className="ml-auto flex gap-2">
            <button
              onClick={() => onComposite(task.id)}
              disabled={task.composite_status === 'processing'}
              className="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-xs flex items-center gap-1 disabled:opacity-50"
            >
              {task.composite_status === 'processing' ? (
                <Loader2 className="animate-spin" size={14} />
              ) : task.composite_status === 'completed' ? (
                <RefreshCw size={14} />
              ) : (
                <Wand2 size={14} />
              )}
              {task.composite_status === 'completed' ? '重新合成' : '合成'}
            </button>
          </div>
        </div>
      </div>

      {/* Step C-D: Prompt & LipSync */}
      <div className="p-4">
        <span className="text-xs font-medium text-gray-500 uppercase mb-2 block">
          Step C-D: 口播生成
        </span>
        <div className="space-y-3">
          {/* Prompt */}
          <div>
            <label className="text-xs text-gray-400 mb-1 block">动作提示词</label>
            <input
              type="text"
              value={editPrompt}
              onChange={(e) => setEditPrompt(e.target.value)}
              onBlur={() => {
                if (editPrompt !== task.motion_prompt) {
                  onUpdatePrompt(task.id, editPrompt, editAudio)
                }
              }}
              placeholder="微笑介绍，自然手势..."
              className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-lg"
            />
          </div>

          {/* Audio */}
          <div>
            <label className="text-xs text-gray-400 mb-1 block">音频片段</label>
            <input
              ref={audioInputRef}
              type="file"
              accept="audio/*"
              onChange={handleAudioChange}
              className="hidden"
            />
            {task.audio_segment_url ? (
              <div className="flex items-center gap-3 p-2 bg-gray-50 border border-gray-200 rounded-lg">
                <Music size={16} className="text-blue-500 shrink-0" />
                <audio src={task.audio_segment_url} controls className="h-8 flex-1 max-w-[300px]" />
                <button
                  onClick={() => audioInputRef.current?.click()}
                  disabled={uploadingAudio}
                  className="text-xs text-blue-600 hover:text-blue-700 shrink-0"
                >
                  {uploadingAudio ? <Loader2 className="animate-spin" size={12} /> : '更换'}
                </button>
              </div>
            ) : (
              <button
                onClick={() => audioInputRef.current?.click()}
                disabled={uploadingAudio}
                className="flex items-center gap-2 px-3 py-2 border border-dashed border-gray-300 rounded-lg text-gray-400 hover:border-blue-400 hover:text-blue-500 transition-colors text-sm"
              >
                {uploadingAudio ? (
                  <Loader2 className="animate-spin" size={16} />
                ) : (
                  <Upload size={16} />
                )}
                上传音频文件
              </button>
            )}
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => onLipSync(task.id)}
              disabled={task.lipsync_status === 'processing'}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm flex items-center gap-2 disabled:opacity-50"
            >
              {task.lipsync_status === 'processing' ? (
                <Loader2 className="animate-spin" size={16} />
              ) : (
                <Mic size={16} />
              )}
              {task.lipsync_status === 'completed' ? '重新生成' : '生成口播视频'}
            </button>
            <StatusBadge status={task.lipsync_status} />
            {task.error_message && (
              <span className="text-xs text-red-500">{task.error_message}</span>
            )}
          </div>

          {/* Video preview */}
          {task.lipsync_status === 'completed' && task.video_url && (
            <div className="mt-3">
              <video
                src={task.video_url}
                controls
                className="w-full max-w-md rounded-lg"
              />
              <div className="flex items-center gap-2 mt-2">
                <span className="text-xs text-gray-400">
                  {task.duration_seconds ? `${task.duration_seconds.toFixed(1)}s` : ''}
                </span>
                <span className="flex items-center gap-1 text-xs text-green-600">
                  <Check size={12} />
                  已自动加入视频池
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  if (status === 'processing') {
    return (
      <span className="flex items-center gap-1 text-xs text-blue-600">
        <Loader2 className="animate-spin" size={12} /> 处理中
      </span>
    )
  }
  if (status === 'completed') {
    return (
      <span className="flex items-center gap-1 text-xs text-green-600">
        <CheckCircle size={12} /> 完成
      </span>
    )
  }
  if (status === 'failed') {
    return (
      <span className="flex items-center gap-1 text-xs text-red-500">
        <XCircle size={12} /> 失败
      </span>
    )
  }
  return <span className="text-xs text-gray-400">待处理</span>
}
