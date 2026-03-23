import type { QualityReport } from '../../types'
import { CheckCircle, AlertTriangle, XCircle } from 'lucide-react'

const DIMENSION_LABELS: Record<string, string> = {
  visual_quality: '画面质量',
  audio_sync: '音画同步',
  subtitle_accuracy: '字幕准确',
  content_alignment: '内容匹配',
  style_consistency: '风格一致',
  pacing: '节奏感',
}

function ScoreBar({ label, score }: { label: string; score: number }) {
  const color = score >= 80 ? 'bg-green-500' : score >= 60 ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-3">
      <div className="w-20 text-xs text-gray-600 text-right">{label}</div>
      <div className="flex-1 bg-gray-200 rounded-full h-2">
        <div className={`h-2 rounded-full ${color} transition-all`} style={{ width: `${score}%` }} />
      </div>
      <div className="w-10 text-xs text-gray-700 text-right">{score.toFixed(0)}</div>
    </div>
  )
}

export default function PipelineReport({ report }: { report: QualityReport }) {
  const scoreColor = report.overall_score >= 80 ? 'text-green-600' : report.overall_score >= 60 ? 'text-yellow-600' : 'text-red-600'

  return (
    <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-200 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-gray-900">质量评审报告</h3>
        <div className="flex items-center gap-2">
          {report.passed ? (
            <CheckCircle size={20} className="text-green-500" />
          ) : (
            <XCircle size={20} className="text-red-500" />
          )}
          <span className={`text-2xl font-bold ${scoreColor}`}>
            {report.overall_score.toFixed(1)}
          </span>
        </div>
      </div>

      {/* Dimension scores */}
      <div className="space-y-2">
        {Object.entries(report.dimension_scores).map(([key, score]) => (
          <ScoreBar key={key} label={DIMENSION_LABELS[key] || key} score={score} />
        ))}
      </div>

      {/* Issues */}
      {report.issues.length > 0 && (
        <div>
          <h4 className="text-sm font-medium text-gray-700 mb-2">问题列表</h4>
          <div className="space-y-2">
            {report.issues.map((issue, i) => (
              <div key={i} className={`px-4 py-3 rounded-lg ${
                issue.severity === 'critical' ? 'bg-red-50 border border-red-200' :
                issue.severity === 'major' ? 'bg-yellow-50 border border-yellow-200' :
                'bg-gray-50 border border-gray-200'
              }`}>
                <div className="flex items-center gap-2 mb-1">
                  {issue.severity === 'critical' ? (
                    <XCircle size={14} className="text-red-500" />
                  ) : (
                    <AlertTriangle size={14} className="text-yellow-500" />
                  )}
                  <span className="text-sm font-medium text-gray-900">{issue.description}</span>
                </div>
                <div className="text-xs text-gray-500 pl-5">
                  <span className="mr-3">{issue.timestamp}</span>
                  {issue.suggestion}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Rollback info */}
      {report.rollback_level && (
        <div className="px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg text-sm">
          <span className="font-medium text-amber-800">回退: </span>
          <span className="text-amber-700">
            {report.rollback_level} → {report.rollback_target}
            {report.retry_count > 0 && ` (第 ${report.retry_count} 次重试)`}
          </span>
        </div>
      )}
    </div>
  )
}
