import { useState, useEffect, useRef, useCallback } from 'react'
import { DndContext, PointerSensor, useSensor, useSensors, useDroppable, useDraggable } from '@dnd-kit/core'
import type { DragEndEvent } from '@dnd-kit/core'
import { SortableContext, horizontalListSortingStrategy, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useTimelineStore, type TLClip, type TrackType } from '../../stores/timelineStore'
import { getSelectedVideos } from '../../api/generation'
import { getTimeline, saveTimeline, uploadTimelineAsset, deleteTimelineAsset } from '../../api/timeline'
import type { GeneratedVideo, TimelineAsset } from '../../types'
import {
  Film, GripVertical, Trash2, ZoomIn, ZoomOut, Save,
  Upload, Music, Type, Video, X, Loader2,
} from 'lucide-react'
import { cn } from '../../lib/utils'

interface Props {
  projectId: string
}

// ─── Track configuration ───
const TRACK_CONFIG: { type: TrackType; label: string; color: string; icon: typeof Film }[] = [
  { type: 'video', label: '视频轨', color: 'indigo', icon: Video },
  { type: 'audio', label: '音频轨', color: 'emerald', icon: Music },
  { type: 'subtitle', label: '字幕轨', color: 'amber', icon: Type },
]

const TRACK_COLORS: Record<TrackType, { bg: string; border: string; text: string }> = {
  video: { bg: 'bg-blue-50', border: 'border-blue-300', text: 'text-blue-600' },
  audio: { bg: 'bg-emerald-50', border: 'border-emerald-300', text: 'text-emerald-600' },
  subtitle: { bg: 'bg-amber-50', border: 'border-amber-300', text: 'text-amber-600' },
}

// ─── Pixel <-> time helpers ───
function msToPixels(ms: number, zoomLevel: number) {
  return (ms / 1000) * (zoomLevel / 100) * 80
}

// ─── Draggable pool item ───
function PoolItem({ id, children }: { id: string; children: React.ReactNode }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id })
  return (
    <div ref={setNodeRef} {...attributes} {...listeners} style={{ opacity: isDragging ? 0.4 : 1 }} className="cursor-grab">
      {children}
    </div>
  )
}

// ─── Sortable clip on track ───
function SortableClip({ clip, zoomLevel, onRemove }: { clip: TLClip; zoomLevel: number; onRemove: (id: string) => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: clip.id })
  const colors = TRACK_COLORS[clip.trackType]
  const widthPx = Math.max(msToPixels(clip.durationMs, zoomLevel), 60)

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    width: `${widthPx}px`,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(colors.bg, colors.border, 'border rounded-md h-12 flex items-center px-2 gap-1 shrink-0 group overflow-hidden')}
    >
      <button {...attributes} {...listeners} className="cursor-grab text-gray-400 hover:text-gray-600 shrink-0">
        <GripVertical size={12} />
      </button>
      <div className="flex-1 min-w-0">
        <div className={cn('text-[11px] truncate', colors.text)}>{clip.label || clip.filename || '片段'}</div>
        <div className="text-[9px] text-gray-400">{(clip.durationMs / 1000).toFixed(1)}s</div>
      </div>
      <button onClick={() => onRemove(clip.id)} className="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
        <X size={10} />
      </button>
    </div>
  )
}

// ─── Track drop zone ───
function TrackDropZone({ trackType, children }: { trackType: TrackType; children: React.ReactNode }) {
  const { setNodeRef, isOver } = useDroppable({ id: `track-${trackType}` })
  return (
    <div
      ref={setNodeRef}
      className={cn(
        'min-h-[56px] rounded-lg border-2 border-dashed transition-colors p-1',
        isOver ? 'border-blue-400 bg-blue-50' : 'border-gray-200 bg-gray-50/50',
      )}
    >
      {children}
    </div>
  )
}

// ─── Main component ───
export default function TimelineEditor({ projectId }: Props) {
  const { clips, setClips, addClip, removeClip, zoomLevel, setZoomLevel } = useTimelineStore()
  const [availableVideos, setAvailableVideos] = useState<GeneratedVideo[]>([])
  const [assets, setAssets] = useState<TimelineAsset[]>([])
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  // ─── Load data ───
  useEffect(() => {
    getSelectedVideos(projectId).then(setAvailableVideos)
    getTimeline(projectId).then((tl) => {
      setClips(tl.clips.map((c) => ({
        id: c.id,
        generatedVideoId: c.generated_video_id,
        assetId: c.asset_id,
        trackType: (c.track_type || 'video') as TrackType,
        trackIndex: c.track_index,
        positionMs: c.position_ms,
        durationMs: c.duration_ms,
        sortOrder: c.sort_order,
        label: c.label,
        videoUrl: c.video_url,
        thumbnailUrl: c.thumbnail_url,
        filename: c.filename,
      })))
      setAssets(tl.assets || [])
    })
  }, [projectId, setClips])

  // ─── Auto-save ───
  const autoSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(async () => {
      setSaving(true)
      const clipsData = useTimelineStore.getState().clips
      await saveTimeline(projectId, clipsData.map((c, i) => ({
        generated_video_id: c.generatedVideoId,
        asset_id: c.assetId,
        track_type: c.trackType,
        track_index: c.trackIndex,
        position_ms: c.positionMs,
        duration_ms: c.durationMs,
        sort_order: i,
        label: c.label,
      })))
      setSaving(false)
    }, 800)
  }, [projectId])

  // ─── Add clip from pool to track ───
  const addVideoToTrack = (video: GeneratedVideo, trackType: TrackType) => {
    const trackClips = clips.filter((c) => c.trackType === trackType)
    const lastClip = trackClips[trackClips.length - 1]
    const positionMs = lastClip ? lastClip.positionMs + lastClip.durationMs : 0
    const newClip: TLClip = {
      id: `clip-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      generatedVideoId: video.id,
      assetId: null,
      trackType,
      trackIndex: 0,
      positionMs,
      durationMs: (video.duration_seconds || 5) * 1000,
      sortOrder: trackClips.length,
      label: video.material_filename || `视频`,
      videoUrl: video.video_url,
      thumbnailUrl: video.thumbnail_url,
      filename: video.material_filename,
    }
    addClip(newClip)
    autoSave()
  }

  const addAssetToTrack = (asset: TimelineAsset, trackType: TrackType) => {
    const trackClips = clips.filter((c) => c.trackType === trackType)
    const lastClip = trackClips[trackClips.length - 1]
    const positionMs = lastClip ? lastClip.positionMs + lastClip.durationMs : 0
    const newClip: TLClip = {
      id: `clip-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      generatedVideoId: null,
      assetId: asset.id,
      trackType,
      trackIndex: 0,
      positionMs,
      durationMs: asset.duration_ms || 5000,
      sortOrder: trackClips.length,
      label: asset.filename,
      videoUrl: asset.file_url,
      thumbnailUrl: null,
      filename: asset.filename,
    }
    addClip(newClip)
    autoSave()
  }

  const handleRemove = (id: string) => {
    removeClip(id)
    autoSave()
  }

  // ─── File upload ───
  const handleFileUpload = async (fileList: FileList | null) => {
    if (!fileList) return
    setUploading(true)
    try {
      for (let i = 0; i < fileList.length; i++) {
        const asset = await uploadTimelineAsset(projectId, fileList[i])
        setAssets((prev) => [...prev, asset])
      }
    } catch (e) {
      console.error('Upload failed:', e)
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDeleteAsset = async (assetId: string) => {
    await deleteTimelineAsset(assetId)
    setAssets((prev) => prev.filter((a) => a.id !== assetId))
    const updated = clips.filter((c) => c.assetId !== assetId)
    setClips(updated)
    autoSave()
  }

  // ─── DnD handlers ───
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over) return

    const activeId = String(active.id)
    const overId = String(over.id)

    // Dropping a pool item onto a track
    if (overId.startsWith('track-')) {
      const targetTrack = overId.replace('track-', '') as TrackType

      if (activeId.startsWith('pool-video-')) {
        const videoId = activeId.replace('pool-video-', '')
        const video = availableVideos.find((v) => v.id === videoId)
        if (video) addVideoToTrack(video, targetTrack)
        return
      }

      if (activeId.startsWith('pool-asset-')) {
        const assetId = activeId.replace('pool-asset-', '')
        const asset = assets.find((a) => a.id === assetId)
        if (asset) addAssetToTrack(asset, targetTrack)
        return
      }
    }

    // Reordering within same track
    if (!overId.startsWith('track-') && !activeId.startsWith('pool-')) {
      const activeClip = clips.find((c) => c.id === activeId)
      const overClip = clips.find((c) => c.id === overId)
      if (!activeClip || !overClip || activeClip.trackType !== overClip.trackType) return

      const trackType = activeClip.trackType
      const trackClips = clips.filter((c) => c.trackType === trackType)
      const otherClips = clips.filter((c) => c.trackType !== trackType)

      const oldIndex = trackClips.findIndex((c) => c.id === activeId)
      const newIndex = trackClips.findIndex((c) => c.id === overId)
      if (oldIndex === -1 || newIndex === -1) return

      const reordered = [...trackClips]
      const [moved] = reordered.splice(oldIndex, 1)
      reordered.splice(newIndex, 0, moved)

      let pos = 0
      for (const c of reordered) {
        c.positionMs = pos
        pos += c.durationMs
      }

      setClips([...otherClips, ...reordered])
      autoSave()
    }
  }

  // ─── Computed ───
  const maxDuration = clips.reduce((max, c) => Math.max(max, c.positionMs + c.durationMs), 0)
  const rulerWidth = Math.max(msToPixels(maxDuration, zoomLevel) + 200, 800)
  const rulerSeconds = Math.ceil(maxDuration / 1000) + 5

  const videoAssets = assets.filter((a) => a.asset_type === 'video')
  const audioAssets = assets.filter((a) => a.asset_type === 'audio')
  const subtitleAssets = assets.filter((a) => a.asset_type === 'subtitle')

  return (
    <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
      <div className="flex h-full">
        {/* Left: Asset pool */}
        <div className="w-60 bg-gray-50 border-r border-gray-200 overflow-y-auto shrink-0 flex flex-col">
          {/* Generated videos */}
          <div className="p-3 border-b border-gray-200">
            <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">生成的视频</div>
            {availableVideos.length === 0 ? (
              <p className="text-xs text-gray-400">暂无选中的视频</p>
            ) : (
              <div className="space-y-1.5">
                {availableVideos.map((video, i) => (
                  <PoolItem key={video.id} id={`pool-video-${video.id}`}>
                    <div
                      className="flex items-center gap-2 p-1.5 bg-white rounded-lg hover:bg-gray-100 transition-colors border border-gray-200"
                      onClick={() => addVideoToTrack(video, 'video')}
                    >
                      <div className="w-12 h-8 bg-gray-100 rounded overflow-hidden shrink-0 flex items-center justify-center">
                        {video.material_thumbnail_url ? (
                          <img src={video.material_thumbnail_url} className="w-full h-full object-cover" alt="" />
                        ) : (
                          <Film size={14} className="text-gray-400" />
                        )}
                      </div>
                      <div className="min-w-0">
                        <p className="text-[11px] text-gray-700 truncate">{video.material_filename || `视频 #${i + 1}`}</p>
                        <p className="text-[9px] text-gray-400">{video.duration_seconds?.toFixed(1)}s</p>
                      </div>
                    </div>
                  </PoolItem>
                ))}
              </div>
            )}
          </div>

          {/* Local assets upload */}
          <div className="p-3 border-b border-gray-200">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-gray-400 uppercase tracking-wider">本地素材</span>
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="text-xs px-2 py-0.5 bg-blue-50 text-blue-600 hover:bg-blue-100 rounded flex items-center gap-1"
              >
                {uploading ? <Loader2 size={10} className="animate-spin" /> : <Upload size={10} />}
                上传
              </button>
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                multiple
                accept="video/*,audio/*,.srt,.vtt,.ass,.ssa,.lrc"
                onChange={(e) => handleFileUpload(e.target.files)}
              />
            </div>

            {/* Videos */}
            {videoAssets.length > 0 && (
              <div className="mb-2">
                <div className="text-[10px] text-gray-400 mb-1 flex items-center gap-1"><Video size={10} /> 视频</div>
                {videoAssets.map((a) => (
                  <PoolItem key={a.id} id={`pool-asset-${a.id}`}>
                    <div className="flex items-center justify-between p-1 bg-white rounded mb-1 hover:bg-gray-100 group border border-gray-200"
                      onClick={() => addAssetToTrack(a, 'video')}>
                      <span className="text-[11px] text-gray-700 truncate flex-1">{a.filename}</span>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDeleteAsset(a.id) }}
                        className="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 shrink-0 ml-1"
                      ><X size={10} /></button>
                    </div>
                  </PoolItem>
                ))}
              </div>
            )}

            {/* Audio */}
            {audioAssets.length > 0 && (
              <div className="mb-2">
                <div className="text-[10px] text-gray-400 mb-1 flex items-center gap-1"><Music size={10} /> 音频</div>
                {audioAssets.map((a) => (
                  <PoolItem key={a.id} id={`pool-asset-${a.id}`}>
                    <div className="flex items-center justify-between p-1 bg-white rounded mb-1 hover:bg-gray-100 group border border-gray-200"
                      onClick={() => addAssetToTrack(a, 'audio')}>
                      <span className="text-[11px] text-emerald-700 truncate flex-1">{a.filename}</span>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDeleteAsset(a.id) }}
                        className="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 shrink-0 ml-1"
                      ><X size={10} /></button>
                    </div>
                  </PoolItem>
                ))}
              </div>
            )}

            {/* Subtitles */}
            {subtitleAssets.length > 0 && (
              <div className="mb-2">
                <div className="text-[10px] text-gray-400 mb-1 flex items-center gap-1"><Type size={10} /> 字幕</div>
                {subtitleAssets.map((a) => (
                  <PoolItem key={a.id} id={`pool-asset-${a.id}`}>
                    <div className="flex items-center justify-between p-1 bg-white rounded mb-1 hover:bg-gray-100 group border border-gray-200"
                      onClick={() => addAssetToTrack(a, 'subtitle')}>
                      <span className="text-[11px] text-amber-700 truncate flex-1">{a.filename}</span>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDeleteAsset(a.id) }}
                        className="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 shrink-0 ml-1"
                      ><X size={10} /></button>
                    </div>
                  </PoolItem>
                ))}
              </div>
            )}

            {assets.length === 0 && (
              <p className="text-[11px] text-gray-400">上传视频、音频或字幕文件</p>
            )}
          </div>
        </div>

        {/* Right: Timeline area */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Controls bar */}
          <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b border-gray-200 shrink-0">
            <span className="text-xs text-gray-400">
              时长: {(maxDuration / 1000).toFixed(1)}s | 片段: {clips.length}
            </span>
            <div className="flex items-center gap-2">
              <button onClick={() => setZoomLevel(Math.max(50, zoomLevel - 25))} className="text-gray-400 hover:text-gray-700">
                <ZoomOut size={16} />
              </button>
              <span className="text-xs text-gray-400 w-10 text-center">{zoomLevel}%</span>
              <button onClick={() => setZoomLevel(Math.min(300, zoomLevel + 25))} className="text-gray-400 hover:text-gray-700">
                <ZoomIn size={16} />
              </button>
              {saving && (
                <span className="text-xs text-blue-500 flex items-center gap-1">
                  <Save size={12} /> 保存中...
                </span>
              )}
            </div>
          </div>

          {/* Timeline with ruler and tracks */}
          <div className="flex-1 overflow-auto bg-white">
            {/* Ruler */}
            <div className="sticky top-0 z-10 bg-white border-b border-gray-200 pl-20">
              <div className="flex items-center h-6" style={{ width: `${rulerWidth}px` }}>
                {Array.from({ length: rulerSeconds }, (_, i) => (
                  <div key={i} className="flex-shrink-0 text-[10px] text-gray-400 border-l border-gray-200 pl-1"
                    style={{ width: `${(zoomLevel / 100) * 80}px` }}>
                    {i}s
                  </div>
                ))}
              </div>
            </div>

            {/* Tracks */}
            <div className="p-3 space-y-2">
              {TRACK_CONFIG.map(({ type, label, icon: Icon }) => {
                const trackClips = clips.filter((c) => c.trackType === type)
                const colors = TRACK_COLORS[type]
                return (
                  <div key={type} className="flex items-stretch gap-0">
                    {/* Track label */}
                    <div className="w-20 shrink-0 flex items-center gap-1.5 pr-2">
                      <Icon size={14} className={colors.text} />
                      <span className={cn('text-xs', colors.text)}>{label}</span>
                    </div>

                    {/* Track content */}
                    <div className="flex-1" style={{ minWidth: `${rulerWidth}px` }}>
                      <TrackDropZone trackType={type}>
                        {trackClips.length === 0 ? (
                          <div className="flex items-center justify-center h-12 text-gray-400 text-[11px]">
                            拖拽或点击素材添加到此轨道
                          </div>
                        ) : (
                          <SortableContext items={trackClips.map((c) => c.id)} strategy={horizontalListSortingStrategy}>
                            <div className="flex gap-1 items-center min-h-[48px]">
                              {trackClips.map((clip) => (
                                <SortableClip key={clip.id} clip={clip} zoomLevel={zoomLevel} onRemove={handleRemove} />
                              ))}
                            </div>
                          </SortableContext>
                        )}
                      </TrackDropZone>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    </DndContext>
  )
}
