import { useState, useEffect } from 'react'
import { Archive, ArrowLeft, Check, Download, Film, FolderOpen, Image, Play, Trash2, Video } from 'lucide-react'
import { deleteUserUpload, listUserUploads, listUserDeliveries } from '../../api/repository'
import { getCategories, getMaterials } from '../../api/materials'
import type { RepositoryUpload, RepositoryDelivery, MaterialItem, MaterialCategory } from '../../types'
import { cn } from '../../lib/utils'
import { useToast } from '../ui/Toast'

type Tab = 'uploads' | 'materials' | 'deliveries'

interface Props {
  onBack: () => void
  onPickerConfirm?: (items: MaterialItem[]) => void
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('zh-CN', { year: 'numeric', month: 'short', day: 'numeric' })
}

export default function RepositoryPage({ onBack, onPickerConfirm }: Props) {
  const pickerMode = !!onPickerConfirm
  const [tab, setTab] = useState<Tab>(pickerMode ? 'materials' : 'deliveries')
  const [pickerSelectedItems, setPickerSelectedItems] = useState<Map<string, MaterialItem>>(new Map())
  const { toast } = useToast()

  // Uploads
  const [uploads, setUploads] = useState<RepositoryUpload[]>([])
  const [loadingUploads, setLoadingUploads] = useState(false)
  const [playingUploadId, setPlayingUploadId] = useState<string | null>(null)

  // Materials
  const [categories, setCategories] = useState<MaterialCategory[]>([])
  const [activeCategory, setActiveCategory] = useState('')
  const [materials, setMaterials] = useState<MaterialItem[]>([])
  const [loadingMaterials, setLoadingMaterials] = useState(false)

  // Deliveries
  const [deliveries, setDeliveries] = useState<RepositoryDelivery[]>([])
  const [loadingDeliveries, setLoadingDeliveries] = useState(false)
  const [playingDeliveryId, setPlayingDeliveryId] = useState<string | null>(null)

  useEffect(() => {
    if (tab === 'uploads' && uploads.length === 0) {
      setLoadingUploads(true)
      listUserUploads().then(setUploads).catch(() => toast('error', '加载上传视频失败')).finally(() => setLoadingUploads(false))
    }
    if (tab === 'materials' && categories.length === 0) {
      setLoadingMaterials(true)
      getCategories().then(cats => {
        setCategories(cats)
        if (cats.length > 0) setActiveCategory(cats[0].name)
      }).catch(() => toast('error', '加载素材分类失败')).finally(() => setLoadingMaterials(false))
    }
    if (tab === 'deliveries' && deliveries.length === 0) {
      setLoadingDeliveries(true)
      listUserDeliveries().then(setDeliveries).catch(() => toast('error', '加载生成视频失败')).finally(() => setLoadingDeliveries(false))
    }
  }, [tab])

  useEffect(() => {
    if (!activeCategory) return
    setLoadingMaterials(true)
    getMaterials(activeCategory, 1, 200)
      .then(r => setMaterials(r.items))
      .catch(() => toast('warning', '加载素材列表失败'))
      .finally(() => setLoadingMaterials(false))
  }, [activeCategory])

  const handleDeleteUpload = async (upload: RepositoryUpload) => {
    const confirmed = window.confirm(`确认删除视频「${upload.filename}」吗？删除后无法恢复。`)
    if (!confirmed) return
    try {
      await deleteUserUpload(upload.id)
      setUploads((prev) => prev.filter((item) => item.id !== upload.id))
      if (playingUploadId === upload.id) {
        setPlayingUploadId(null)
      }
      toast('success', '已从视频仓库删除')
    } catch (error: any) {
      toast('error', error?.userMessage || '删除上传视频失败')
    }
  }

  const tabs: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: 'deliveries', label: '生成视频', icon: <Film size={14} /> },
    { key: 'uploads', label: '上传视频', icon: <Video size={14} /> },
    { key: 'materials', label: '素材库', icon: <Image size={14} /> },
  ]

  return (
    <div className="h-full flex flex-col bg-white">
      <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="inline-flex items-center gap-2 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          >
            <ArrowLeft size={16} />
            {pickerMode ? '取消，返回对话' : '返回'}
          </button>
          <div className="flex items-center gap-2">
            <Archive size={16} className="text-blue-500" />
            <span className="text-base font-semibold text-gray-900">
              {pickerMode ? '从素材库选择' : '我的仓库'}
            </span>
          </div>
        </div>
        {pickerMode && (
          <button
            onClick={() => {
              onPickerConfirm!(Array.from(pickerSelectedItems.values()))
            }}
            disabled={pickerSelectedItems.size === 0}
            className="rounded-full bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40 transition-colors"
          >
            确认选择{pickerSelectedItems.size > 0 ? ` (${pickerSelectedItems.size})` : ''}
          </button>
        )}
      </div>

      <div className="px-6 pt-4 border-b border-gray-200">
        <div className="flex gap-1">
          {tabs.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                'inline-flex items-center gap-2 px-4 py-2.5 text-sm rounded-t-lg border-b-2 transition-colors',
                tab === t.key
                  ? 'border-blue-600 text-blue-700 font-medium bg-blue-50'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50',
              )}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {tab === 'deliveries' && (
          <div>
            {loadingDeliveries ? (
              <div className="text-sm text-gray-500 text-center py-12">加载中...</div>
            ) : deliveries.length === 0 ? (
              <div className="text-center py-16 text-gray-400">
                <Film size={40} className="mx-auto mb-3 opacity-30" />
                <div className="text-sm">还没有保存到仓库的视频</div>
                <div className="text-xs mt-1">生成视频后点击「保存到视频仓库」即可在这里查看</div>
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {deliveries.map(d => (
                  <div key={d.id} className="rounded-2xl border border-gray-200 overflow-hidden bg-white shadow-sm">
                    {d.video_url ? (
                      playingDeliveryId === d.id ? (
                        <video src={d.video_url} controls autoPlay className="w-full aspect-video bg-black" />
                      ) : (
                        <button
                          onClick={() => setPlayingDeliveryId(d.id)}
                          className="w-full aspect-video bg-slate-900 flex items-center justify-center group relative"
                        >
                          <video src={d.video_url} className="w-full h-full object-cover opacity-60" muted />
                          <div className="absolute inset-0 flex items-center justify-center">
                            <div className="w-12 h-12 rounded-full bg-white/20 flex items-center justify-center group-hover:bg-white/30 transition-colors">
                              <Play size={20} className="text-white ml-1" />
                            </div>
                          </div>
                        </button>
                      )
                    ) : (
                      <div className="w-full aspect-video bg-slate-100 flex items-center justify-center">
                        <Film size={32} className="text-slate-300" />
                      </div>
                    )}
                    <div className="p-3">
                      <div className="text-sm font-medium text-gray-900 truncate">{d.title || '未命名视频'}</div>
                      <div className="text-xs text-gray-400 mt-1 flex items-center gap-2">
                        <FolderOpen size={11} />
                        <span className="truncate">{d.project_name}</span>
                        <span>·</span>
                        <span>{formatDate(d.created_at)}</span>
                      </div>
                      {d.video_url && (
                        <a
                          href={d.video_url}
                          download
                          className="mt-2 inline-flex items-center gap-1.5 text-xs text-blue-600 hover:text-blue-800"
                        >
                          <Download size={11} /> 下载
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {tab === 'uploads' && (
          <div>
            {loadingUploads ? (
              <div className="text-sm text-gray-500 text-center py-12">加载中...</div>
            ) : uploads.length === 0 ? (
              <div className="text-center py-16 text-gray-400">
                <Video size={40} className="mx-auto mb-3 opacity-30" />
                <div className="text-sm">还没有上传过参考视频</div>
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {uploads.map(u => (
                  <div key={u.id} className="rounded-2xl border border-gray-200 overflow-hidden bg-white shadow-sm">
                    {playingUploadId === u.id ? (
                      <video src={u.stream_url} controls autoPlay className="w-full aspect-video bg-black" />
                    ) : (
                      <button
                        onClick={() => setPlayingUploadId(u.id)}
                        className="w-full aspect-video bg-slate-900 flex items-center justify-center group"
                      >
                        <div className="w-12 h-12 rounded-full bg-white/20 flex items-center justify-center group-hover:bg-white/30 transition-colors">
                          <Play size={20} className="text-white ml-1" />
                        </div>
                      </button>
                    )}
                    <div className="p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-gray-900 truncate">{u.filename}</div>
                          <div className="text-xs text-gray-400 mt-1 flex items-center gap-2">
                            <FolderOpen size={11} />
                            <span className="truncate">{u.project_name}</span>
                            <span>·</span>
                            <span>{formatBytes(u.file_size)}</span>
                            {u.duration_seconds && <><span>·</span><span>{u.duration_seconds.toFixed(1)}s</span></>}
                          </div>
                          <div className="text-xs text-gray-300 mt-0.5">{formatDate(u.created_at)}</div>
                        </div>
                        <button
                          onClick={() => handleDeleteUpload(u).catch(() => {})}
                          className="shrink-0 rounded-lg p-2 text-gray-400 hover:bg-red-50 hover:text-red-500 transition-colors"
                          title="删除上传视频"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {tab === 'materials' && (
          <div className="flex gap-6 h-full">
            <div className="w-44 shrink-0">
              <div className="text-xs uppercase tracking-widest text-gray-400 mb-3">分类</div>
              <div className="space-y-1">
                {categories.map(cat => (
                  <button
                    key={cat.name}
                    onClick={() => setActiveCategory(cat.name)}
                    className={cn(
                      'w-full text-left px-3 py-2 rounded-lg text-sm transition-colors',
                      activeCategory === cat.name
                        ? 'bg-blue-50 text-blue-700 font-medium'
                        : 'text-gray-600 hover:bg-gray-100',
                    )}
                  >
                    {cat.name}
                    <span className="ml-1.5 text-xs opacity-50">({cat.count})</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="flex-1">
              {loadingMaterials ? (
                <div className="text-sm text-gray-500 text-center py-12">加载中...</div>
              ) : materials.length === 0 ? (
                <div className="text-center py-16 text-gray-400">
                  <Image size={40} className="mx-auto mb-3 opacity-30" />
                  <div className="text-sm">该分类下暂无素材</div>
                </div>
              ) : (
                <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-3">
                  {materials.map(item => {
                    const selected = pickerSelectedItems.has(item.id)
                    return (
                      <div
                        key={item.id}
                        onClick={pickerMode ? () => {
                          setPickerSelectedItems(prev => {
                            const next = new Map(prev)
                            if (next.has(item.id)) {
                              next.delete(item.id)
                            } else {
                              next.set(item.id, item)
                            }
                            return next
                          })
                        } : undefined}
                        className={cn(
                          'rounded-xl overflow-hidden border bg-white relative',
                          pickerMode ? 'cursor-pointer transition-all' : '',
                          selected ? 'border-blue-500 ring-2 ring-blue-300' : 'border-gray-200',
                        )}
                      >
                        <div className="aspect-[4/3] bg-gray-100">
                          {item.media_type.startsWith('video/') ? (
                            <div className="w-full h-full flex items-center justify-center bg-slate-800">
                              <Film size={20} className="text-slate-400" />
                            </div>
                          ) : (
                            <img src={item.thumbnail_url || ''} alt={item.filename} className="w-full h-full object-cover" />
                          )}
                        </div>
                        <div className="px-2 py-1.5">
                          <div className="text-xs text-gray-700 truncate">{item.filename}</div>
                        </div>
                        {selected && (
                          <div className="absolute top-1.5 right-1.5 h-5 w-5 rounded-full bg-blue-600 flex items-center justify-center shadow">
                            <Check size={11} className="text-white" />
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
