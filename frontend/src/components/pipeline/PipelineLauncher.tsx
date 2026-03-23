import { useState, useEffect } from 'react'
import { usePipelineStore } from '../../stores/pipelineStore'
import { launchPipeline, listPipelines } from '../../api/pipeline'
import { getSelectedMaterials } from '../../api/materials'
import type { PipelineRun, MaterialSelection } from '../../types'
import { Rocket, Clock, ChevronRight } from 'lucide-react'

const PLATFORM_OPTIONS = [
  { value: 'generic', label: '通用 16:9' },
  { value: 'douyin', label: '抖音 9:16' },
  { value: 'xiaohongshu', label: '小红书 3:4' },
  { value: 'bilibili', label: 'B站 16:9' },
]

const STYLE_OPTIONS = [
  { value: 'commercial', label: '商业广告' },
  { value: 'lifestyle', label: '生活方式' },
  { value: 'cinematic', label: '电影感' },
]

export default function PipelineLauncher({ projectId }: { projectId: string }) {
  const { setCurrentRun } = usePipelineStore()
  const [script, setScript] = useState('')
  const [platform, setPlatform] = useState('generic')
  const [duration, setDuration] = useState(30)
  const [style, setStyle] = useState('commercial')
  const [launching, setLaunching] = useState(false)
  const [selections, setSelections] = useState<MaterialSelection[]>([])
  const [pastRuns, setPastRuns] = useState<PipelineRun[]>([])

  useEffect(() => {
    getSelectedMaterials(projectId).then(setSelections).catch(() => {})
    listPipelines(projectId).then(setPastRuns).catch(() => {})
  }, [projectId])

  const handleLaunch = async () => {
    if (!script.trim() || selections.length === 0) return
    setLaunching(true)
    try {
      const run = await launchPipeline(projectId, {
        script: script.trim(),
        image_ids: selections.map(s => s.material_id),
        platform,
        duration_seconds: duration,
        style,
        voice_id: 'default',
      })
      setCurrentRun(run)
    } catch (err) {
      console.error('Failed to launch pipeline:', err)
    } finally {
      setLaunching(false)
    }
  }

  const statusLabel = (status: string) => {
    const map: Record<string, string> = {
      pending: '等待中',
      running: '运行中',
      completed: '已完成',
      failed: '失败',
      cancelled: '已取消',
    }
    return map[status] || status
  }

  const statusColor = (status: string) => {
    const map: Record<string, string> = {
      pending: 'text-yellow-600',
      running: 'text-blue-600',
      completed: 'text-green-600',
      failed: 'text-red-600',
      cancelled: 'text-gray-500',
    }
    return map[status] || 'text-gray-500'
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-200">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">一键生成营销视频</h2>

        {/* Script */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">营销脚本</label>
          <textarea
            value={script}
            onChange={(e) => setScript(e.target.value)}
            rows={5}
            placeholder="输入您的营销文案脚本..."
            className="w-full bg-gray-50 text-gray-900 rounded-lg px-4 py-3 text-sm border border-gray-300 focus:border-blue-500 focus:outline-none resize-none"
          />
        </div>

        {/* Selected materials */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            已选素材 ({selections.length} 张)
          </label>
          {selections.length === 0 ? (
            <p className="text-sm text-gray-400">请先在"素材库"步骤中选择图片素材</p>
          ) : (
            <div className="flex gap-2 flex-wrap">
              {selections.map(s => (
                <div key={s.id} className="w-16 h-16 rounded-lg bg-gray-100 border border-gray-200 overflow-hidden">
                  {s.material?.thumbnail_url && (
                    <img src={s.material.thumbnail_url} alt="" className="w-full h-full object-cover" />
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Options row */}
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">目标平台</label>
            <select
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="w-full bg-gray-50 text-gray-900 rounded-lg px-3 py-2 text-sm border border-gray-300 focus:border-blue-500 focus:outline-none"
            >
              {PLATFORM_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">视频风格</label>
            <select
              value={style}
              onChange={(e) => setStyle(e.target.value)}
              className="w-full bg-gray-50 text-gray-900 rounded-lg px-3 py-2 text-sm border border-gray-300 focus:border-blue-500 focus:outline-none"
            >
              {STYLE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">时长 ({duration}s)</label>
            <input
              type="range"
              min={10}
              max={120}
              step={5}
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
              className="w-full mt-2"
            />
          </div>
        </div>

        {/* Launch button */}
        <button
          onClick={handleLaunch}
          disabled={launching || !script.trim() || selections.length === 0}
          className="w-full px-6 py-3 bg-blue-600 hover:bg-blue-500 text-white rounded-lg flex items-center justify-center gap-2 disabled:opacity-50 transition-colors"
        >
          <Rocket size={18} />
          {launching ? '启动中...' : '启动 Agent 流水线'}
        </button>
      </div>

      {/* Past runs */}
      {pastRuns.length > 0 && (
        <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-200">
          <h3 className="text-sm font-semibold text-gray-900 mb-3">历史运行记录</h3>
          <div className="space-y-2">
            {pastRuns.map(run => (
              <button
                key={run.id}
                onClick={() => setCurrentRun(run)}
                className="w-full text-left px-4 py-3 bg-gray-50 hover:bg-gray-100 rounded-lg flex items-center justify-between transition-colors"
              >
                <div>
                  <div className="text-sm text-gray-900">
                    <span className={statusColor(run.status)}>{statusLabel(run.status)}</span>
                    {run.overall_score != null && (
                      <span className="ml-2 text-gray-500">得分: {run.overall_score.toFixed(1)}</span>
                    )}
                  </div>
                  <div className="text-xs text-gray-500 flex items-center gap-2 mt-0.5">
                    <Clock size={12} />
                    {new Date(run.created_at).toLocaleString()}
                    {run.current_agent && <span>| 当前: {run.current_agent}</span>}
                  </div>
                </div>
                <ChevronRight size={16} className="text-gray-400" />
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
