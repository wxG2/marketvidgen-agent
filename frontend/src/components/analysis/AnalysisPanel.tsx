import { useState, useEffect } from 'react'
import { Brain, Loader2, AlertCircle, Tag, FolderOpen } from 'lucide-react'
import { triggerAnalysis, getAnalysis } from '../../api/analysis'
import { getVideoStreamUrl } from '../../api/upload'
import { getUpload } from '../../api/upload'
import type { VideoAnalysis, VideoUpload } from '../../types'

interface Props {
  projectId: string
  upload: VideoUpload | null
  onComplete: (analysis: VideoAnalysis) => void
}

export default function AnalysisPanel({ projectId, upload, onComplete }: Props) {
  const [analysis, setAnalysis] = useState<VideoAnalysis | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    getAnalysis(projectId).then(setAnalysis).catch(() => {})
  }, [projectId])

  useEffect(() => {
    if (!analysis || (analysis.status !== 'pending' && analysis.status !== 'processing')) return
    const timer = setInterval(async () => {
      const updated = await getAnalysis(projectId)
      setAnalysis(updated)
      if (updated.status === 'completed') {
        onComplete(updated)
        clearInterval(timer)
      } else if (updated.status === 'failed') {
        clearInterval(timer)
      }
    }, 2000)
    return () => clearInterval(timer)
  }, [analysis?.status, projectId, onComplete])

  const handleStart = async () => {
    setLoading(true)
    setError('')
    try {
      const result = await triggerAnalysis(projectId)
      setAnalysis(result)
    } catch (e: any) {
      setError(e.response?.data?.detail || '分析启动失败')
    } finally {
      setLoading(false)
    }
  }

  const isProcessing = analysis?.status === 'pending' || analysis?.status === 'processing'
  const hasResult = analysis?.status === 'completed' || analysis?.status === 'failed'

  return (
    <div className="flex h-full">
      {/* Left: Video preview */}
      <div className="w-1/2 border-r border-gray-200 flex flex-col">
        <div className="p-4 border-b border-gray-200">
          <h3 className="text-sm font-medium text-gray-700">上传的视频</h3>
        </div>
        <div className="flex-1 flex items-center justify-center bg-gray-50 p-4">
          {upload ? (
            <video
              src={getVideoStreamUrl(upload.id)}
              controls
              className="w-full max-h-full rounded-lg"
            />
          ) : (
            <p className="text-gray-400 text-sm">请先在上一步上传视频</p>
          )}
        </div>
      </div>

      {/* Right: Analysis results */}
      <div className="w-1/2 overflow-y-auto">
        <div className="p-6">
          {!analysis && (
            <div className="text-center py-12">
              <Brain className="mx-auto text-gray-400 mb-4" size={56} />
              <h2 className="text-lg text-gray-900 mb-2">AI 视频理解</h2>
              <p className="text-gray-500 mb-6 text-sm">使用 AI 分析视频内容，自动识别场景类型并推荐素材分类</p>
              <button
                onClick={handleStart}
                disabled={loading}
                className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors disabled:opacity-50 flex items-center gap-2 mx-auto text-sm"
              >
                {loading ? <Loader2 className="animate-spin" size={16} /> : <Brain size={16} />}
                开始分析
              </button>
              {error && <p className="mt-4 text-red-500 flex items-center gap-2 justify-center text-sm"><AlertCircle size={14} />{error}</p>}
            </div>
          )}

          {isProcessing && (
            <div className="text-center py-12">
              <Loader2 className="mx-auto text-blue-500 animate-spin mb-4" size={48} />
              <h2 className="text-lg text-gray-900 mb-2">正在分析视频...</h2>
              <p className="text-gray-500 text-sm">AI 正在理解视频内容，请稍候</p>
            </div>
          )}

          {analysis?.status === 'completed' && (
            <div className="space-y-4">
              <div className="bg-gray-50 rounded-xl p-5 border border-gray-200">
                <h3 className="text-gray-900 font-medium mb-2 flex items-center gap-2 text-sm">
                  <Brain size={16} className="text-blue-600" />分析摘要
                </h3>
                <p className="text-gray-700 leading-relaxed text-sm">{analysis.summary}</p>
              </div>

              {analysis.scene_tags && (
                <div className="bg-gray-50 rounded-xl p-5 border border-gray-200">
                  <h3 className="text-gray-900 font-medium mb-2 flex items-center gap-2 text-sm">
                    <Tag size={16} className="text-blue-600" />场景标签
                  </h3>
                  <div className="flex flex-wrap gap-2">
                    {analysis.scene_tags.map((tag) => (
                      <span key={tag} className="px-3 py-1 bg-blue-50 text-blue-700 rounded-full text-xs border border-blue-200">{tag}</span>
                    ))}
                  </div>
                </div>
              )}

              {analysis.recommended_categories && (
                <div className="bg-gray-50 rounded-xl p-5 border border-gray-200">
                  <h3 className="text-gray-900 font-medium mb-2 flex items-center gap-2 text-sm">
                    <FolderOpen size={16} className="text-blue-600" />推荐素材分类
                  </h3>
                  <div className="flex flex-wrap gap-2">
                    {analysis.recommended_categories.map((cat) => (
                      <span key={cat} className="px-3 py-1 bg-green-50 text-green-700 rounded-lg text-xs border border-green-200">{cat}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {analysis?.status === 'failed' && (
            <div className="text-center py-12">
              <AlertCircle className="mx-auto text-red-400 mb-4" size={48} />
              <h2 className="text-lg text-gray-900 mb-2">分析失败</h2>
              <p className="text-red-500 text-sm">{analysis.error_message}</p>
              <button onClick={handleStart} className="mt-4 px-6 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-sm">
                重新分析
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
