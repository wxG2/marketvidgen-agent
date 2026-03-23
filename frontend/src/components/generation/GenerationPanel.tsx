import { useState, useEffect } from 'react'
import { startGeneration, generateSingle, getGenerations, selectVideo, deselectVideo } from '../../api/generation'
import { getPromptBindings } from '../../api/prompts'
import type { GeneratedVideo, PromptBinding } from '../../types'
import { Wand2, Loader2, CheckCircle, XCircle, Check, Image as ImageIcon, Mic, Video } from 'lucide-react'
import { cn } from '../../lib/utils'
import TalkingHeadPanel from '../talking-head/TalkingHeadPanel'

interface Props {
  projectId: string
  onSelectionChange?: () => void
}

export default function GenerationPanel({ projectId, onSelectionChange }: Props) {
  const [activeTab, setActiveTab] = useState<'image_to_video' | 'talking_head'>('image_to_video')
  const [bindings, setBindings] = useState<PromptBinding[]>([])
  const [videos, setVideos] = useState<GeneratedVideo[]>([])
  const [generatingAll, setGeneratingAll] = useState(false)
  const [generatingIds, setGeneratingIds] = useState<Set<string>>(new Set())
  const [polling, setPolling] = useState(false)

  useEffect(() => {
    getPromptBindings(projectId).then(setBindings)
    getGenerations(projectId).then((v) => {
      setVideos(v)
      if (v.some((x) => x.status === 'processing' || x.status === 'pending')) {
        setPolling(true)
      }
    })
  }, [projectId])

  useEffect(() => {
    if (!polling) return
    const timer = setInterval(async () => {
      const updated = await getGenerations(projectId)
      setVideos(updated)
      if (!updated.some((v) => v.status === 'processing' || v.status === 'pending')) {
        setPolling(false)
        setGeneratingIds(new Set())
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [polling, projectId])

  const handleGenerateAll = async () => {
    setGeneratingAll(true)
    try {
      const result = await startGeneration(projectId)
      setVideos(result)
      setPolling(true)
    } finally {
      setGeneratingAll(false)
    }
  }

  const handleGenerateSingle = async (promptId: string) => {
    setGeneratingIds((prev) => new Set(prev).add(promptId))
    try {
      const result = await generateSingle(projectId, promptId)
      setVideos((prev) => [...prev, result])
      setPolling(true)
    } catch {
      setGeneratingIds((prev) => { const n = new Set(prev); n.delete(promptId); return n })
    }
  }

  const toggleSelect = async (video: GeneratedVideo) => {
    if (video.is_selected) {
      await deselectVideo(projectId, video.id)
    } else {
      await selectVideo(projectId, video.id)
    }
    setVideos((prev) =>
      prev.map((v) => v.id === video.id ? { ...v, is_selected: !v.is_selected } : v)
    )
    onSelectionChange?.()
  }

  const videosByPrompt = videos.reduce<Record<string, GeneratedVideo[]>>((acc, v) => {
    if (!acc[v.prompt_id]) acc[v.prompt_id] = []
    acc[v.prompt_id].push(v)
    return acc
  }, {})

  const selectedCount = videos.filter((v) => v.is_selected).length

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex border-b border-gray-200 px-6 pt-2 shrink-0">
        <button
          onClick={() => setActiveTab('image_to_video')}
          className={cn(
            'px-4 py-2.5 text-sm font-medium flex items-center gap-2 border-b-2 transition-colors -mb-px',
            activeTab === 'image_to_video'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700',
          )}
        >
          <Video size={16} />
          图生视频
        </button>
        <button
          onClick={() => setActiveTab('talking_head')}
          className={cn(
            'px-4 py-2.5 text-sm font-medium flex items-center gap-2 border-b-2 transition-colors -mb-px',
            activeTab === 'talking_head'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700',
          )}
        >
          <Mic size={16} />
          口播视频
        </button>
      </div>

      {/* Tab content */}
      {activeTab === 'talking_head' ? (
        <TalkingHeadPanel projectId={projectId} onSelectionChange={onSelectionChange} />
      ) : bindings.length === 0 && videos.length === 0 ? (
        <div className="p-6 text-center text-gray-400 mt-20">
          <Wand2 className="mx-auto mb-4" size={64} />
          <h2 className="text-xl text-gray-900 mb-2">暂无绑定数据</h2>
          <p>请先在上一步生成提示词</p>
        </div>
      ) : (
      <div className="flex flex-1 min-h-0">
      {/* Main content */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg text-gray-900 font-medium">素材 + 提示词 → 视频生成</h2>
            <p className="text-sm text-gray-500 mt-1">
              选择要生成的绑定，或一键全部生成。生成后可预览并选择最终视频。
            </p>
          </div>
          <div className="flex items-center gap-3">
            {selectedCount > 0 && (
              <span className="text-sm text-blue-600">已选 {selectedCount} 个视频</span>
            )}
            <button
              onClick={handleGenerateAll}
              disabled={generatingAll || bindings.length === 0}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm flex items-center gap-2 disabled:opacity-50"
            >
              {generatingAll ? <Loader2 className="animate-spin" size={16} /> : <Wand2 size={16} />}
              全部生成
            </button>
          </div>
        </div>

        {/* Binding cards */}
        <div className="space-y-4">
          {bindings.map((binding) => {
            const promptVideos = videosByPrompt[binding.prompt_id] || []
            const isGenerating = generatingIds.has(binding.prompt_id) ||
              promptVideos.some((v) => v.status === 'processing' || v.status === 'pending')

            return (
              <div key={binding.prompt_id} className="bg-white rounded-xl overflow-hidden border border-gray-200">
                <div className="flex items-start gap-4 p-4 border-b border-gray-100">
                  <div className="w-24 h-24 rounded-lg overflow-hidden bg-gray-100 shrink-0">
                    {binding.material_thumbnail_url ? (
                      <img
                        src={binding.material_thumbnail_url}
                        alt={binding.material_filename || ''}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-gray-300">
                        <ImageIcon size={32} />
                      </div>
                    )}
                  </div>

                  <div className="flex-1 min-w-0">
                    {binding.material_category && (
                      <span className="text-xs text-blue-600 mb-1 block">
                        {binding.material_category}
                        {binding.material_filename && (
                          <span className="text-gray-400 ml-1">/ {binding.material_filename}</span>
                        )}
                      </span>
                    )}
                    <p className="text-sm text-gray-700 leading-relaxed line-clamp-3">
                      {binding.prompt_text}
                    </p>
                  </div>

                  <button
                    onClick={() => handleGenerateSingle(binding.prompt_id)}
                    disabled={isGenerating}
                    className="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-xs flex items-center gap-1 disabled:opacity-50 shrink-0"
                  >
                    {isGenerating ? <Loader2 className="animate-spin" size={14} /> : <Wand2 size={14} />}
                    {promptVideos.length > 0 ? '重新生成' : '生成'}
                  </button>
                </div>

                {promptVideos.length > 0 && (
                  <div className="p-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                      {promptVideos.map((video) => (
                        <div
                          key={video.id}
                          className={cn(
                            'rounded-lg overflow-hidden border-2 transition-all',
                            video.is_selected ? 'border-blue-500 ring-1 ring-blue-500/30' : 'border-gray-200',
                          )}
                        >
                          <div className="aspect-video bg-gray-50 relative flex items-center justify-center">
                            {(video.status === 'processing' || video.status === 'pending') ? (
                              <div className="text-center">
                                <Loader2 className="animate-spin text-blue-500 mx-auto" size={28} />
                                <p className="text-xs text-gray-400 mt-2">生成中...</p>
                              </div>
                            ) : video.status === 'completed' ? (
                              video.video_url ? (
                                <video src={video.video_url} className="w-full h-full object-cover" controls />
                              ) : (
                                <div className="text-center">
                                  <CheckCircle className="text-green-500 mx-auto" size={28} />
                                  <p className="text-xs text-gray-500 mt-1">生成完成</p>
                                </div>
                              )
                            ) : (
                              <div className="text-center">
                                <XCircle className="text-red-400 mx-auto" size={28} />
                                <p className="text-xs text-red-500 mt-1 px-2 truncate">{video.error_message || '生成失败'}</p>
                              </div>
                            )}
                          </div>
                          {video.status === 'completed' && (
                            <div className="p-2 flex items-center justify-between bg-gray-50">
                              <span className="text-xs text-gray-400">
                                {video.duration_seconds ? `${video.duration_seconds.toFixed(1)}s` : ''}
                              </span>
                              <button
                                onClick={() => toggleSelect(video)}
                                className={cn(
                                  'px-2.5 py-1 rounded text-xs flex items-center gap-1 transition-colors',
                                  video.is_selected
                                    ? 'bg-blue-600 text-white'
                                    : 'bg-gray-200 text-gray-700 hover:bg-gray-300',
                                )}
                              >
                                <Check size={12} />
                                {video.is_selected ? '已选中' : '选择'}
                              </button>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Right sidebar: selected summary */}
      {selectedCount > 0 && (
        <div className="w-64 bg-gray-50 border-l border-gray-200 overflow-y-auto shrink-0">
          <div className="p-3 text-xs text-gray-400 uppercase tracking-wider">
            已选视频 ({selectedCount})
          </div>
          {videos.filter((v) => v.is_selected).map((video) => (
            <div key={video.id} className="px-3 py-2 border-b border-gray-200">
              <div className="flex items-center gap-2 mb-1">
                {video.material_thumbnail_url && (
                  <img
                    src={video.material_thumbnail_url}
                    alt=""
                    className="w-8 h-8 rounded object-cover"
                  />
                )}
                <div className="min-w-0">
                  <p className="text-xs text-gray-900 truncate">{video.material_filename || '素材'}</p>
                  <p className="text-[11px] text-gray-400">{video.material_category}</p>
                </div>
              </div>
              <p className="text-[11px] text-gray-500 line-clamp-2">{video.prompt_text}</p>
              <button
                onClick={() => toggleSelect(video)}
                className="mt-1 text-[11px] text-red-500 hover:text-red-600"
              >
                取消选择
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
      )}
    </div>
  )
}
