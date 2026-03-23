import { useState, useEffect, useRef } from 'react'
import { usePipelineStore } from '../../stores/pipelineStore'
import { getPipelineRun, getPipelineAgents, getPipelineUsage, cancelPipeline } from '../../api/pipeline'
import type { AgentExecution } from '../../types'
import PipelineUsageDashboard from './PipelineUsageDashboard'
import NodePreview from './NodePreview'
import { ArrowLeft, StopCircle, Loader, X } from 'lucide-react'

/* ── Agent metadata ── */
const AGENT_META: Record<string, { label: string; desc: string }> = {
  orchestrator:     { label: '调度 Agent',       desc: '任务拆解与流程编排' },
  prompt_engineer:  { label: '提示词设计 Agent', desc: '分镜与 Prompt 生成' },
  audio_subtitle:   { label: '音频字幕 Agent',   desc: 'TTS 与字幕时间轴' },
  video_generator:  { label: '视频生成 Agent',   desc: 'AI 视频片段生成' },
  video_editor:     { label: '视频剪辑 Agent',   desc: '合成与精剪' },
}

/* ── Node positions (within a 960×620 SVG viewBox) ── */
interface NodePos { x: number; y: number; w: number; h: number }

const NODES: Record<string, NodePos> = {
  user:             { x: 380, y: 10,  w: 140, h: 44 },
  orchestrator:     { x: 380, y: 110, w: 140, h: 44 },
  prompt_engineer:  { x: 380, y: 210, w: 140, h: 44 },
  audio_subtitle:   { x: 140, y: 320, w: 140, h: 44 },
  video_generator:  { x: 620, y: 320, w: 140, h: 44 },
  video_editor:     { x: 380, y: 430, w: 140, h: 44 },
}

/* ── Edge definitions (from → to, with optional label and waypoints) ── */
interface EdgeDef {
  from: string; to: string; label?: string
  points?: { x: number; y: number }[]  // intermediate waypoints
}

const EDGES: EdgeDef[] = [
  { from: 'user',            to: 'orchestrator',    label: '图片+脚本' },
  { from: 'orchestrator',    to: 'prompt_engineer', label: '需求解析' },
  { from: 'prompt_engineer', to: 'audio_subtitle',  label: '角色、语气' },
  { from: 'prompt_engineer', to: 'video_generator', label: '分镜、Prompt' },
  { from: 'audio_subtitle',  to: 'video_editor',    label: '音频+字幕' },
  { from: 'video_generator', to: 'video_editor',    label: '短视频' },
]

/* ── Helper: build SVG path string ── */
function edgePath(e: EdgeDef): string {
  const fromN = NODES[e.from]
  const toN = NODES[e.to]
  const sx = fromN.x + fromN.w / 2
  const sy = fromN.y + fromN.h
  const ex = toN.x + toN.w / 2
  const ey = toN.y

  if (e.points && e.points.length > 0) {
    const pts = e.points.map(p => `L${p.x},${p.y}`).join(' ')
    return `M${sx},${sy} ${pts} L${ex},${ey}`
  }
  // Simple curve
  const my = (sy + ey) / 2
  return `M${sx},${sy} C${sx},${my} ${ex},${my} ${ex},${ey}`
}

/* ── Flowing dot animation on an edge ── */
function FlowingDots({ path, active }: { path: string; active: boolean }) {
  if (!active) return null
  return (
    <>
      {[0, 1, 2].map(i => (
        <circle key={i} r="3.5" fill="#3b82f6" opacity="0.9">
          <animateMotion
            dur="1.8s"
            repeatCount="indefinite"
            begin={`${i * 0.6}s`}
            path={path}
          />
        </circle>
      ))}
    </>
  )
}

/* ── Single graph node ── */
function GraphNode({
  id, pos, status, label, desc, duration, attempt, isAgent, selected, onClick,
}: {
  id: string; pos: NodePos; status: string; label: string; desc?: string
  duration?: number | null; attempt?: number; isAgent: boolean
  selected: boolean; onClick?: () => void
}) {
  const bgMap: Record<string, string> = {
    completed: '#ecfdf5',
    running:   '#eff6ff',
    failed:    '#fef2f2',
    pending:   '#f9fafb',
  }
  const borderMap: Record<string, string> = {
    completed: '#86efac',
    running:   '#93c5fd',
    failed:    '#fca5a5',
    pending:   '#e5e7eb',
  }
  const bg = bgMap[status] || bgMap.pending
  const border = borderMap[status] || borderMap.pending

  return (
    <g
      className={isAgent ? 'cursor-pointer' : ''}
      onClick={isAgent ? onClick : undefined}
    >
      {/* Glow ring when running */}
      {status === 'running' && (
        <rect
          x={pos.x - 3} y={pos.y - 3}
          width={pos.w + 6} height={pos.h + 6}
          rx={14} fill="none" stroke="#93c5fd" strokeWidth="2"
          opacity="0.6"
        >
          <animate attributeName="opacity" values="0.6;0.2;0.6" dur="1.5s" repeatCount="indefinite" />
        </rect>
      )}

      {/* Selection highlight */}
      {selected && (
        <rect
          x={pos.x - 4} y={pos.y - 4}
          width={pos.w + 8} height={pos.h + 8}
          rx={14} fill="none" stroke="#3b82f6" strokeWidth="2.5"
        />
      )}

      {/* Main rect */}
      <rect
        x={pos.x} y={pos.y}
        width={pos.w} height={pos.h}
        rx={12} fill={bg} stroke={border} strokeWidth="1.5"
      />

      {/* Status icon */}
      {status === 'completed' && (
        <circle cx={pos.x + 16} cy={pos.y + pos.h / 2} r={5} fill="#22c55e" />
      )}
      {status === 'running' && (
        <circle cx={pos.x + 16} cy={pos.y + pos.h / 2} r={5} fill="#3b82f6">
          <animate attributeName="r" values="4;6;4" dur="1s" repeatCount="indefinite" />
        </circle>
      )}
      {status === 'failed' && (
        <circle cx={pos.x + 16} cy={pos.y + pos.h / 2} r={5} fill="#ef4444" />
      )}
      {status === 'pending' && isAgent && (
        <circle cx={pos.x + 16} cy={pos.y + pos.h / 2} r={5} fill="#d1d5db" />
      )}

      {/* Label */}
      <text
        x={pos.x + (isAgent ? 28 : pos.w / 2)}
        y={pos.y + (desc ? 17 : pos.h / 2 + 1)}
        fontSize="12" fontWeight="600"
        fill="#111827"
        textAnchor={isAgent ? 'start' : 'middle'}
        dominantBaseline={desc ? 'auto' : 'central'}
      >
        {label}
      </text>

      {/* Sub-label */}
      {desc && (
        <text
          x={pos.x + 28} y={pos.y + 32}
          fontSize="9" fill="#6b7280"
          textAnchor="start"
        >
          {duration != null ? `${(duration / 1000).toFixed(1)}s` : desc}
          {attempt != null && attempt > 1 ? ` (第${attempt}次)` : ''}
        </text>
      )}
    </g>
  )
}


/* ── Main component ── */
export default function PipelineProgress({ projectId }: { projectId: string }) {
  const {
    currentRun,
    setCurrentRun,
    agentExecutions,
    setAgentExecutions,
    usageSummary,
    setUsageSummary,
    reset,
  } = usePipelineStore()
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)

  /* Polling logic (unchanged) */
  useEffect(() => {
    if (!currentRun) return
    const poll = async () => {
      try {
        const run = await getPipelineRun(projectId, currentRun.id)
        setCurrentRun(run)
        const agents = await getPipelineAgents(projectId, currentRun.id)
        setAgentExecutions(agents)
        try {
          const usage = await getPipelineUsage(projectId, currentRun.id)
          setUsageSummary(usage)
        } catch {}
        if (run.status === 'completed' || run.status === 'failed') {
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
        }
      } catch {}
    }
    poll()
    pollRef.current = setInterval(poll, 3000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [currentRun?.id, projectId])

  if (!currentRun) return null

  const isRunning = currentRun.status === 'running' || currentRun.status === 'pending'

  // Latest execution per agent
  const agentMap = new Map<string, AgentExecution>()
  for (const exec of agentExecutions) {
    const existing = agentMap.get(exec.agent_name)
    if (!existing || new Date(exec.created_at) > new Date(existing.created_at))
      agentMap.set(exec.agent_name, exec)
  }

  const getStatus = (name: string) => agentMap.get(name)?.status || 'pending'

  // Determine if an edge should animate
  const isEdgeActive = (e: EdgeDef) => {
    const fromStatus = e.from === 'user' ? 'completed' : getStatus(e.from)
    const toStatus = getStatus(e.to)
    // Active when source completed and target is running
    if (fromStatus === 'completed' && toStatus === 'running') return true
    return false
  }

  // Edge is "done" (solid) when both ends completed
  const isEdgeDone = (e: EdgeDef) => {
    const fromDone = e.from === 'user' ? true : getStatus(e.from) === 'completed'
    const toDone = getStatus(e.to) === 'completed'
    return fromDone && toDone
  }

  const isRollbackEdge = (e: EdgeDef) => !!e.label?.includes('回退')

  return (
    <div className="h-full flex flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-gray-200 bg-white">
        <button onClick={() => reset()} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
          <ArrowLeft size={16} /> 返回
        </button>
        <div className="flex items-center gap-3">
          {isRunning && <Loader size={16} className="text-blue-500 animate-spin" />}
          <span className={`text-sm font-medium ${
            currentRun.status === 'completed' ? 'text-green-600' :
            currentRun.status === 'failed' ? 'text-red-600' :
            'text-blue-600'
          }`}>
            {currentRun.status === 'completed' ? '已完成' :
             currentRun.status === 'failed' ? '失败' :
             currentRun.status === 'cancelled' ? '已取消' :
             currentRun.status === 'running' ? '运行中' : '等待中'}
          </span>
          {isRunning && (
            <button
              onClick={async () => {
                await cancelPipeline(projectId, currentRun.id)
                const run = await getPipelineRun(projectId, currentRun.id)
                setCurrentRun(run)
              }}
              className="flex items-center gap-1 px-3 py-1 text-sm text-red-600 hover:bg-red-50 rounded-lg"
            >
              <StopCircle size={14} /> 取消
            </button>
          )}
        </div>
      </div>

      {/* Graph + Preview side-by-side */}
      <div className="flex-1 flex overflow-hidden">
        {/* SVG Graph */}
        <div className={`flex-1 overflow-auto bg-gray-50 flex items-center justify-center transition-all ${selectedNode ? 'w-[60%]' : 'w-full'}`}>
          <svg viewBox="0 0 900 620" className="w-full max-w-[900px] h-auto p-4" xmlns="http://www.w3.org/2000/svg">
            {/* Defs for arrowheads */}
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 Z" fill="#9ca3af" />
              </marker>
              <marker id="arrow-blue" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 Z" fill="#3b82f6" />
              </marker>
              <marker id="arrow-green" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 Z" fill="#22c55e" />
              </marker>
              <marker id="arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 Z" fill="#f87171" />
              </marker>
            </defs>

            {/* Edges */}
            {EDGES.map((e, i) => {
              const path = edgePath(e)
              const active = isEdgeActive(e)
              const done = isEdgeDone(e)
              const rollback = isRollbackEdge(e)

              const strokeColor = active ? '#3b82f6' : done ? '#22c55e' : rollback ? '#f8717180' : '#d1d5db'
              const markerEnd = active ? 'url(#arrow-blue)' : done ? 'url(#arrow-green)' : rollback ? 'url(#arrow-red)' : 'url(#arrow)'
              const dashArray = rollback ? '6,4' : undefined

              return (
                <g key={i}>
                  <path
                    d={path} fill="none"
                    stroke={strokeColor}
                    strokeWidth={active ? 2 : 1.5}
                    strokeDasharray={dashArray}
                    markerEnd={markerEnd}
                  />
                  <FlowingDots path={path} active={active} />
                  {/* Edge label */}
                  {e.label && (
                    <text fontSize="9" fill="#9ca3af" textAnchor="middle">
                      <textPath href={`#edge-path-${i}`} startOffset="50%">
                        {e.label}
                      </textPath>
                    </text>
                  )}
                  <path id={`edge-path-${i}`} d={path} fill="none" stroke="none" />
                </g>
              )
            })}

            {/* User node */}
            <GraphNode
              id="user" pos={NODES.user} status="completed"
              label="用户" isAgent={false} selected={false}
            />

            {/* Agent nodes */}
            {Object.entries(AGENT_META).map(([name, meta]) => {
              const exec = agentMap.get(name)
              return (
                <GraphNode
                  key={name}
                  id={name}
                  pos={NODES[name]}
                  status={exec?.status || 'pending'}
                  label={meta.label}
                  desc={meta.desc}
                  duration={exec?.duration_ms}
                  attempt={exec?.attempt_number}
                  isAgent
                  selected={selectedNode === name}
                  onClick={() => setSelectedNode(selectedNode === name ? null : name)}
                />
              )
            })}

            {/* "Parallel" bracket label */}
            <text x={450} y={308} fontSize="10" fill="#9ca3af" textAnchor="middle">并行执行</text>
            <line x1={280} y1={310} x2={620} y2={310} stroke="#e5e7eb" strokeWidth="1" strokeDasharray="4,3" />

            {/* Final output */}
            {currentRun.status === 'completed' && (
              <g>
                <rect x={380} y={590} width={140} height={30} rx={8} fill="#ecfdf5" stroke="#86efac" strokeWidth="1.5" />
                <text x={450} y={609} fontSize="12" fontWeight="600" fill="#16a34a" textAnchor="middle">成品视频</text>
              </g>
            )}
          </svg>
        </div>

        {/* Side preview panel */}
        {selectedNode && (
          <div className="w-[40%] min-w-[320px] max-w-[480px] border-l border-gray-200 bg-white overflow-y-auto">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
              <h3 className="text-sm font-semibold text-gray-900">
                {AGENT_META[selectedNode]?.label} — 输出预览
              </h3>
              <button onClick={() => setSelectedNode(null)} className="text-gray-400 hover:text-gray-600">
                <X size={16} />
              </button>
            </div>
            <NodePreview
              agentName={selectedNode}
              status={getStatus(selectedNode)}
              execution={agentMap.get(selectedNode) || null}
            />
          </div>
        )}
      </div>

      {/* Quality report at bottom when visible */}
      {(!selectedNode && usageSummary) && (
        <div className="border-t border-gray-200 p-4 bg-white overflow-y-auto max-h-[45%] space-y-4">
          {usageSummary && <PipelineUsageDashboard usage={usageSummary} />}
        </div>
      )}
    </div>
  )
}
