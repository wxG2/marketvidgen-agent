import type { PipelineUsageSummary } from '../../types'

export default function PipelineUsageDashboard({ usage }: { usage: PipelineUsageSummary }) {
  return (
    <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-200 space-y-5">
      <div className="flex items-end justify-between">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">Token 仪表盘</h3>
          <p className="text-sm text-gray-500 mt-1">当前流水线里可统计的模型调用消耗</p>
        </div>
        <div className="text-right">
          <div className="text-3xl font-bold text-gray-900">{usage.total_tokens.toLocaleString()}</div>
          <div className="text-xs text-gray-500">{usage.request_count} 次模型调用</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <MetricCard label="输入 Tokens" value={usage.prompt_tokens} tone="blue" />
        <MetricCard label="输出 Tokens" value={usage.completion_tokens} tone="emerald" />
        <MetricCard label="总 Tokens" value={usage.total_tokens} tone="amber" />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <div className="text-sm font-medium text-gray-700 mb-2">按 Agent</div>
          <div className="space-y-2">
            {usage.by_agent.map((item) => (
              <UsageRow
                key={item.agent_name}
                title={item.agent_name}
                subtitle={`${item.request_count} 次调用`}
                total={item.total_tokens}
              />
            ))}
          </div>
        </div>
        <div>
          <div className="text-sm font-medium text-gray-700 mb-2">按模型</div>
          <div className="space-y-2">
            {usage.by_model.map((item) => (
              <UsageRow
                key={`${item.provider}-${item.model_name}`}
                title={`${item.provider} / ${item.model_name}`}
                subtitle={`${item.request_count} 次调用`}
                total={item.total_tokens}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function MetricCard({ label, value, tone }: { label: string; value: number; tone: 'blue' | 'emerald' | 'amber' }) {
  const toneClass = {
    blue: 'bg-blue-50 text-blue-700 border-blue-200',
    emerald: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
  }[tone]

  return (
    <div className={`rounded-xl border p-4 ${toneClass}`}>
      <div className="text-xs font-medium">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value.toLocaleString()}</div>
    </div>
  )
}

function UsageRow({ title, subtitle, total }: { title: string; subtitle: string; total: number }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 flex items-center justify-between">
      <div>
        <div className="text-sm font-medium text-gray-900">{title}</div>
        <div className="text-xs text-gray-500 mt-0.5">{subtitle}</div>
      </div>
      <div className="text-sm font-semibold text-gray-800">{total.toLocaleString()}</div>
    </div>
  )
}
