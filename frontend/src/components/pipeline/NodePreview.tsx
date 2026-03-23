import type { AgentExecution } from '../../types'
import { Loader, Clock, AlertTriangle, FileText, Music, Film, CheckCircle } from 'lucide-react'

/**
 * Shows a preview of each agent's output.
 * Uses example files from /examples/ for demonstration.
 */

/* ── Example data mapped per agent ── */

const ORCHESTRATOR_PREVIEW = {
  videoType: '商业广告',
  platform: '通用 (16:9)',
  totalDuration: '30s',
  shots: [
    { idx: 0, segment: '欢迎来到我们的养生馆，这里是您放松身心的理想之地。', duration: '7.5s', image: '环境大景' },
    { idx: 1, segment: '专业的按摩师为您提供个性化的理疗服务。', duration: '7.5s', image: '按摩特写' },
    { idx: 2, segment: '品一杯香茶，享受片刻宁静。', duration: '7.5s', image: '煮茶场景' },
    { idx: 3, segment: '养生馆，让健康成为一种生活方式。', duration: '7.5s', image: '门头外观' },
  ],
}

const PROMPT_ENGINEER_PREVIEW = {
  voiceParams: { voice: '专业知性女声', speed: '1.0x', tone: '自信沉稳' },
  shotPrompts: [
    { idx: 0, prompt: 'A 7-second smooth dolly forward shot. Scene depicts: welcoming spa environment. Visual style: professional commercial quality, clean composition, bright lighting, 4K.' },
    { idx: 1, prompt: 'A 7-second gentle pan left to right shot. Scene depicts: professional massage service. Visual style: warm tones, soft focus background, 4K.' },
    { idx: 2, prompt: 'A 7-second slow push-in shot. Scene depicts: tea ceremony moment. Visual style: cinematic, warm amber lighting, 4K.' },
    { idx: 3, prompt: 'A 7-second elegant tracking shot. Scene depicts: storefront exterior. Visual style: golden hour, inviting atmosphere, 4K.' },
  ],
}

const EXAMPLE_VIDEOS = [
  { name: '按摩场景', url: '/examples/图生短视频/按摩.mp4' },
  { name: '煮茶场景', url: '/examples/图生短视频/煮茶.mp4' },
  { name: '门头场景', url: '/examples/图生短视频/门头.mp4' },
  { name: '人物场景', url: '/examples/图生短视频/人多.mp4' },
]

const EXAMPLE_AUDIO = [
  { name: '口播音频', url: '/examples/音频/vivan_口播.mp3' },
]

const EXAMPLE_FINAL_VIDEO = [
  { name: '最终合成视频', url: '/examples/最终合成视频/营销视频3月16日.mp4' },
]

const SUBTITLE_PREVIEW = `1
00:00:00,000 --> 00:00:03,000
欢迎来到我们的养生馆

2
00:00:03,000 --> 00:00:06,000
这里是您放松身心的理想之地

3
00:00:06,000 --> 00:00:09,000
专业的按摩师为您提供个性化的理疗服务

4
00:00:09,000 --> 00:00:12,000
品一杯香茶享受片刻宁静`


interface Props {
  agentName: string
  status: string
  execution: AgentExecution | null
}

export default function NodePreview({ agentName, status, execution }: Props) {
  // Status header
  const statusInfo = (
    <div className={`flex items-center gap-2 px-4 py-2 text-sm ${
      status === 'completed' ? 'bg-green-50 text-green-700' :
      status === 'running' ? 'bg-blue-50 text-blue-700' :
      status === 'failed' ? 'bg-red-50 text-red-700' :
      'bg-gray-50 text-gray-500'
    }`}>
      {status === 'completed' && <CheckCircle size={14} />}
      {status === 'running' && <Loader size={14} className="animate-spin" />}
      {status === 'failed' && <AlertTriangle size={14} />}
      <span>
        {status === 'completed' ? '已完成' :
         status === 'running' ? '处理中...' :
         status === 'failed' ? '失败' : '等待中'}
      </span>
      {execution?.duration_ms != null && (
        <span className="ml-auto flex items-center gap-1 text-gray-400">
          <Clock size={12} /> {(execution.duration_ms / 1000).toFixed(1)}s
        </span>
      )}
    </div>
  )

  if (status === 'pending') {
    return (
      <div>
        {statusInfo}
        <div className="flex items-center justify-center h-40 text-sm text-gray-400">
          等待执行...
        </div>
      </div>
    )
  }

  if (status === 'running') {
    return (
      <div>
        {statusInfo}
        <div className="flex flex-col items-center justify-center h-40 gap-2">
          <Loader size={24} className="text-blue-400 animate-spin" />
          <span className="text-sm text-gray-500">正在处理中...</span>
        </div>
      </div>
    )
  }

  // Show preview content per agent type
  return (
    <div>
      {statusInfo}
      <div className="p-4 space-y-4">
        {agentName === 'orchestrator' && <OrchestratorPreview />}
        {agentName === 'prompt_engineer' && <PromptEngineerPreview />}
        {agentName === 'audio_subtitle' && <AudioSubtitlePreview />}
        {agentName === 'video_generator' && <VideoGeneratorPreview />}
        {agentName === 'video_editor' && <VideoEditorPreview />}
      </div>
    </div>
  )
}

/* ── Per-agent preview panels ── */

function OrchestratorPreview() {
  const d = ORCHESTRATOR_PREVIEW
  return (
    <>
      <SectionTitle icon={<FileText size={14} />} title="任务解析结果" />
      <div className="grid grid-cols-3 gap-2 text-xs mb-3">
        <InfoChip label="视频类型" value={d.videoType} />
        <InfoChip label="平台格式" value={d.platform} />
        <InfoChip label="总时长" value={d.totalDuration} />
      </div>
      <SectionTitle icon={<Film size={14} />} title="分镜拆解" />
      <div className="space-y-2">
        {d.shots.map(s => (
          <div key={s.idx} className="px-3 py-2 bg-gray-50 rounded-lg text-xs">
            <div className="flex justify-between mb-1">
              <span className="font-medium text-gray-700">镜头 {s.idx + 1}</span>
              <span className="text-gray-400">{s.duration} · {s.image}</span>
            </div>
            <p className="text-gray-600 leading-relaxed">{s.segment}</p>
          </div>
        ))}
      </div>
    </>
  )
}

function PromptEngineerPreview() {
  const d = PROMPT_ENGINEER_PREVIEW
  return (
    <>
      <SectionTitle icon={<Music size={14} />} title="语音设定" />
      <div className="grid grid-cols-3 gap-2 text-xs mb-3">
        <InfoChip label="音色" value={d.voiceParams.voice} />
        <InfoChip label="语速" value={d.voiceParams.speed} />
        <InfoChip label="语气" value={d.voiceParams.tone} />
      </div>
      <SectionTitle icon={<FileText size={14} />} title="视频 Prompt" />
      <div className="space-y-2">
        {d.shotPrompts.map(s => (
          <div key={s.idx} className="px-3 py-2 bg-gray-50 rounded-lg text-xs">
            <span className="font-medium text-gray-700">Shot {s.idx + 1}: </span>
            <span className="text-gray-600">{s.prompt}</span>
          </div>
        ))}
      </div>
    </>
  )
}

function AudioSubtitlePreview() {
  return (
    <>
      <SectionTitle icon={<Music size={14} />} title="生成音频" />
      {EXAMPLE_AUDIO.map(a => (
        <div key={a.name} className="mb-3">
          <p className="text-xs text-gray-500 mb-1">{a.name}</p>
          <audio controls className="w-full h-9" src={a.url} />
        </div>
      ))}
      <SectionTitle icon={<FileText size={14} />} title="字幕预览 (SRT)" />
      <pre className="px-3 py-2 bg-gray-50 rounded-lg text-xs text-gray-600 whitespace-pre-wrap font-mono leading-relaxed">
        {SUBTITLE_PREVIEW}
      </pre>
    </>
  )
}

function VideoGeneratorPreview() {
  return (
    <>
      <SectionTitle icon={<Film size={14} />} title="生成的视频片段" />
      <div className="grid grid-cols-2 gap-3">
        {EXAMPLE_VIDEOS.map(v => (
          <div key={v.name}>
            <video
              src={v.url}
              controls
              muted
              className="w-full rounded-lg bg-black aspect-video"
            />
            <p className="text-xs text-gray-500 mt-1 text-center">{v.name}</p>
          </div>
        ))}
      </div>
    </>
  )
}

function VideoEditorPreview() {
  return (
    <>
      <SectionTitle icon={<Film size={14} />} title="合成成片" />
      {EXAMPLE_FINAL_VIDEO.map(v => (
        <div key={v.name}>
          <video
            src={v.url}
            controls
            className="w-full rounded-lg bg-black aspect-video"
          />
          <p className="text-xs text-gray-500 mt-1 text-center">{v.name}</p>
        </div>
      ))}
      <div className="mt-3 px-3 py-2 bg-gray-50 rounded-lg text-xs text-gray-600">
        <p>合成参数:</p>
        <ul className="mt-1 space-y-0.5 list-disc list-inside text-gray-500">
          <li>分辨率: 1920x1080</li>
          <li>帧率: 30fps</li>
          <li>编码: H.264</li>
          <li>音频: AAC 128kbps</li>
        </ul>
      </div>
    </>
  )
}

/* ── Shared small components ── */

function SectionTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 mb-2 mt-1">
      {icon}
      {title}
    </div>
  )
}

function InfoChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-2 py-1.5 bg-gray-50 rounded-lg">
      <div className="text-[10px] text-gray-400">{label}</div>
      <div className="text-xs font-medium text-gray-700">{value}</div>
    </div>
  )
}
