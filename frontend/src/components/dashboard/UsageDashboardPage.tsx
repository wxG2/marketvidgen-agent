import type { ReactNode } from 'react'
import { useEffect, useMemo, useState } from 'react'
import { deleteProject, getProjectHistory, getProjectUsage, listProjects } from '../../api/projects'
import type { Project, ProjectArtifactFile, ProjectHistoryRun, ProjectUsageSummary } from '../../types'
import { ArrowLeft, BarChart3, ChevronDown, ChevronRight, Clapperboard, Cpu, FileAudio2, FileText, Gauge, FolderKanban, MessageSquareQuote, PlayCircle, Trash2 } from 'lucide-react'

const STATUS_LABELS: Record<string, string> = {
  pending: '等待中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const AGENT_LABELS: Record<string, string> = {
  orchestrator: '调度 Agent',
  prompt_engineer: '提示词设计 Agent',
  audio_subtitle: '音频字幕 Agent',
  video_generator: '视频生成 Agent',
  video_editor: '视频剪辑 Agent',
}

export default function UsageDashboardPage({
  currentProjectId,
  onBack,
}: {
  currentProjectId: string | null
  onBack: () => void
}) {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(currentProjectId)
  const [summary, setSummary] = useState<ProjectUsageSummary | null>(null)
  const [historyRuns, setHistoryRuns] = useState<ProjectHistoryRun[]>([])
  const [expandedRunIds, setExpandedRunIds] = useState<Record<string, boolean>>({})
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    listProjects().then((items) => {
      setProjects(items)
      if (!selectedProjectId && items[0]) setSelectedProjectId(items[0].id)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!selectedProjectId) return
    getProjectUsage(selectedProjectId).then(setSummary).catch(() => setSummary(null))
    getProjectHistory(selectedProjectId).then((data) => {
      setHistoryRuns(data.runs)
      setExpandedRunIds(
        Object.fromEntries(data.runs.slice(0, 1).map((run) => [run.run_id, true])),
      )
    }).catch(() => {
      setHistoryRuns([])
      setExpandedRunIds({})
    })
  }, [selectedProjectId])

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) || null,
    [projects, selectedProjectId],
  )

  return (
    <div className="h-full flex bg-[linear-gradient(180deg,#f8fafc_0%,#eef2ff_100%)]">
      <aside className="w-[320px] shrink-0 border-r border-slate-200 bg-white/85 backdrop-blur p-4 overflow-y-auto">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
        >
          <ArrowLeft size={14} />
          返回工作台
        </button>

        <div className="mt-5">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-400">项目列表</div>
          <div className="mt-3 space-y-2">
            {projects.map((project) => (
              <div
                key={project.id}
                className={`group relative rounded-2xl border px-4 py-3 transition-colors ${
                  selectedProjectId === project.id
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-slate-200 bg-white hover:bg-slate-50'
                }`}
              >
                <button
                  onClick={() => setSelectedProjectId(project.id)}
                  className="w-full text-left"
                >
                  <div className="text-sm font-medium text-slate-900 pr-8">{project.name}</div>
                  <div className="text-xs text-slate-500 mt-1">
                    步骤 {project.current_step}/7 · {new Date(project.created_at).toLocaleDateString()}
                  </div>
                </button>
                {confirmDeleteId === project.id ? (
                  <div className="mt-2 flex items-center gap-2 text-xs">
                    <span className="text-red-600">确认删除？</span>
                    <button
                      disabled={deleting}
                      onClick={async () => {
                        setDeleting(true)
                        try {
                          await deleteProject(project.id)
                          setProjects((prev) => prev.filter((p) => p.id !== project.id))
                          if (selectedProjectId === project.id) {
                            setSelectedProjectId(null)
                            setSummary(null)
                            setHistoryRuns([])
                          }
                        } catch { /* ignore */ }
                        setDeleting(false)
                        setConfirmDeleteId(null)
                      }}
                      className="rounded-lg bg-red-500 px-2 py-0.5 text-white hover:bg-red-600 disabled:opacity-50"
                    >
                      {deleting ? '删除中…' : '确认'}
                    </button>
                    <button
                      onClick={() => setConfirmDeleteId(null)}
                      className="rounded-lg bg-slate-200 px-2 py-0.5 text-slate-700 hover:bg-slate-300"
                    >
                      取消
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(project.id) }}
                    className="absolute top-3 right-3 hidden group-hover:flex items-center justify-center w-7 h-7 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                    title="删除项目"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      </aside>

      <section className="flex-1 overflow-y-auto p-6">
        <div className="max-w-5xl mx-auto space-y-6">
          <div className="rounded-[28px] border border-slate-200 bg-white/90 backdrop-blur p-6 shadow-sm">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-slate-400">
                  <FolderKanban size={14} />
                  项目仪表盘
                </div>
                <h2 className="mt-3 text-2xl font-semibold text-slate-900">
                  {selectedProject?.name || '选择一个项目查看消耗与进度'}
                </h2>
                <p className="mt-2 text-sm text-slate-500">
                  这里会展示项目累计 token 消耗，以及每次流水线产出的提示词、音频、字幕和视频历史。
                </p>
              </div>
              <BarChart3 className="text-blue-500 shrink-0" size={28} />
            </div>
          </div>

          {summary && (
            <>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <MetricCard label="累计总 Tokens" value={summary.total_tokens.toLocaleString()} icon={<Cpu size={16} />} />
                <MetricCard label="累计模型调用" value={summary.request_count.toLocaleString()} icon={<BarChart3 size={16} />} />
                <MetricCard
                  label="最近执行进度"
                  value={summary.latest_pipeline_status ? STATUS_LABELS[summary.latest_pipeline_status] || summary.latest_pipeline_status : '暂无'}
                  icon={<Gauge size={16} />}
                  detail={summary.latest_current_agent ? `当前节点：${AGENT_LABELS[summary.latest_current_agent] || summary.latest_current_agent}` : undefined}
                />
              </div>

              <div className="rounded-[28px] border border-slate-200 bg-white/90 p-6 shadow-sm">
                <div className="text-sm font-semibold text-slate-900 mb-4">流水线记录</div>
                <div className="space-y-3">
                  {summary.pipelines.length === 0 && (
                    <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
                      这个项目还没有流水线运行记录。
                    </div>
                  )}
                  {summary.pipelines.map((pipeline) => (
                    <div key={pipeline.id} className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
                      <div className="flex items-center justify-between gap-4">
                        <div>
                          <div className="text-sm font-medium text-slate-900">
                            {STATUS_LABELS[pipeline.status] || pipeline.status}
                          </div>
                          <div className="text-xs text-slate-500 mt-1">
                            {new Date(pipeline.created_at).toLocaleString()}
                            {pipeline.current_agent && ` · ${AGENT_LABELS[pipeline.current_agent] || pipeline.current_agent}`}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="text-sm font-semibold text-slate-900">{pipeline.total_tokens.toLocaleString()} Tokens</div>
                          <div className="text-xs text-slate-500 mt-1">{pipeline.request_count} 次调用</div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-[28px] border border-slate-200 bg-white/90 p-6 shadow-sm">
                <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <PlayCircle size={16} />
                  项目历史产物
                </div>
                <div className="space-y-4">
                  {historyRuns.length === 0 && (
                    <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
                      这个项目还没有可查看的历史产物。
                    </div>
                  )}
                  {historyRuns.map((run) => {
                    const expanded = !!expandedRunIds[run.run_id]
                    return (
                      <div key={run.run_id} className="overflow-hidden rounded-[24px] border border-slate-200 bg-slate-50">
                        <button
                          onClick={() => setExpandedRunIds((prev) => ({ ...prev, [run.run_id]: !expanded }))}
                          className="flex w-full items-start justify-between gap-4 px-5 py-4 text-left hover:bg-slate-100/80"
                        >
                          <div className="flex items-start gap-3">
                            {expanded ? <ChevronDown size={18} className="mt-0.5 text-slate-500" /> : <ChevronRight size={18} className="mt-0.5 text-slate-500" />}
                            <div>
                              <div className="text-sm font-medium text-slate-900">
                                {new Date(run.created_at).toLocaleString()} · {STATUS_LABELS[run.status] || run.status}
                              </div>
                              <div className="mt-1 text-xs text-slate-500">
                                {run.current_agent ? `当前节点：${AGENT_LABELS[run.current_agent] || run.current_agent} · ` : ''}
                                {run.request_count} 次调用 · {run.total_tokens.toLocaleString()} Tokens
                              </div>
                            </div>
                          </div>
                          <div className="grid shrink-0 grid-cols-2 gap-2 text-xs text-slate-500 md:grid-cols-4">
                            <HistoryStat label="提示词" value={run.prompts.length} />
                            <HistoryStat label="音频" value={run.audio_files.length} />
                            <HistoryStat label="字幕" value={run.subtitle_files.length} />
                            <HistoryStat label="视频" value={run.generated_videos.length + run.final_videos.length} />
                          </div>
                        </button>

                        {expanded && (
                          <div className="border-t border-slate-200 bg-white px-5 py-5">
                            <div className="space-y-5">
                              {run.input_script && (
                                <SectionCard
                                  title="脚本"
                                  icon={<MessageSquareQuote size={15} />}
                                  contentClassName="text-sm leading-7 text-slate-700 whitespace-pre-wrap"
                                >
                                  {run.input_script}
                                </SectionCard>
                              )}

                              <SectionCard
                                title="提示词"
                                icon={<MessageSquareQuote size={15} />}
                              >
                                {run.prompts.length === 0 ? (
                                  <EmptyHint text="本次运行没有记录到提示词。" />
                                ) : (
                                  <div className="space-y-3">
                                    {run.prompts.map((prompt) => (
                                      <div key={`${run.run_id}-${prompt.shot_idx}`} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                                        <div className="text-sm font-medium text-slate-900">分镜 {prompt.shot_idx}</div>
                                        {prompt.script_segment && (
                                          <div className="mt-2 text-xs leading-6 text-slate-500 whitespace-pre-wrap">
                                            文案片段：{prompt.script_segment}
                                          </div>
                                        )}
                                        <div className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">
                                          {prompt.video_prompt}
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </SectionCard>

                              <SectionCard
                                title="音频"
                                icon={<FileAudio2 size={15} />}
                              >
                                {run.audio_files.length === 0 ? (
                                  <EmptyHint text="本次运行没有音频文件。" />
                                ) : (
                                  <div className="grid gap-4 lg:grid-cols-2">
                                    {run.audio_files.map((file) => (
                                      <ArtifactCard key={file.path} file={file} type="audio" />
                                    ))}
                                  </div>
                                )}
                              </SectionCard>

                              <SectionCard
                                title="字幕"
                                icon={<FileText size={15} />}
                              >
                                {run.subtitle_files.length === 0 ? (
                                  <EmptyHint text="本次运行没有字幕文件。" />
                                ) : (
                                  <div className="grid gap-4 lg:grid-cols-2">
                                    {run.subtitle_files.map((file) => (
                                      <ArtifactCard key={file.path} file={file} type="subtitle" />
                                    ))}
                                  </div>
                                )}
                              </SectionCard>

                              <SectionCard
                                title="生成视频"
                                icon={<Clapperboard size={15} />}
                              >
                                {run.generated_videos.length === 0 ? (
                                  <EmptyHint text="本次运行没有分镜视频记录。" />
                                ) : (
                                  <div className="grid gap-4 lg:grid-cols-2">
                                    {run.generated_videos.map((file) => (
                                      <ArtifactCard key={`${file.path}-${file.shot_idx ?? 'na'}`} file={file} type="video" />
                                    ))}
                                  </div>
                                )}
                              </SectionCard>

                              <SectionCard
                                title="最终合成视频"
                                icon={<Clapperboard size={15} />}
                              >
                                {run.final_videos.length === 0 ? (
                                  <EmptyHint text="本次运行还没有最终合成视频。" />
                                ) : (
                                  <div className="grid gap-4 lg:grid-cols-2">
                                    {run.final_videos.map((file) => (
                                      <ArtifactCard key={file.path} file={file} type="video" highlight />
                                    ))}
                                  </div>
                                )}
                              </SectionCard>
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  )
}

function HistoryStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl bg-white px-3 py-2 text-center ring-1 ring-slate-200">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-semibold text-slate-900">{value}</div>
    </div>
  )
}

function SectionCard({
  title,
  icon,
  children,
  contentClassName,
}: {
  title: string
  icon: ReactNode
  children: ReactNode
  contentClassName?: string
}) {
  return (
    <section className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
        {icon}
        {title}
      </div>
      <div className={contentClassName}>{children}</div>
    </section>
  )
}

function EmptyHint({ text }: { text: string }) {
  return <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-4 text-sm text-slate-500">{text}</div>
}

function ArtifactCard({
  file,
  type,
  highlight,
}: {
  file: ProjectArtifactFile
  type: 'audio' | 'video' | 'subtitle'
  highlight?: boolean
}) {
  return (
    <article className={`rounded-2xl border p-4 ${highlight ? 'border-blue-200 bg-blue-50/60' : 'border-slate-200 bg-white'}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-slate-900">{file.name}</div>
          <div className="mt-1 text-xs text-slate-500">
            {file.kind || type}
            {file.shot_idx ? ` · 分镜 ${file.shot_idx}` : ''}
          </div>
        </div>
        <a
          href={file.url}
          target="_blank"
          rel="noreferrer"
          className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs text-white hover:bg-slate-700"
        >
          打开
        </a>
      </div>

      {type === 'audio' && (
        <audio src={file.url} controls className="mt-4 w-full" />
      )}

      {type === 'video' && (
        <video src={file.url} controls className="mt-4 aspect-video w-full rounded-xl bg-black" />
      )}

      {type === 'subtitle' && (
        <pre className="mt-4 max-h-64 overflow-auto rounded-xl bg-slate-950 p-4 text-xs leading-5 text-slate-100 whitespace-pre-wrap">
          {file.content || '字幕文件暂无可预览内容。'}
        </pre>
      )}
    </article>
  )
}

function MetricCard({
  label,
  value,
  icon,
  detail,
}: {
  label: string
  value: string
  icon: ReactNode
  detail?: string
}) {
  return (
    <div className="rounded-[24px] border border-slate-200 bg-white/90 p-5 shadow-sm">
      <div className="flex items-center gap-2 text-sm text-slate-500">
        {icon}
        {label}
      </div>
      <div className="mt-4 text-3xl font-semibold text-slate-900">{value}</div>
      {detail && <div className="mt-2 text-xs text-slate-500">{detail}</div>}
    </div>
  )
}
