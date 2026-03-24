import { useState, useEffect, useCallback } from 'react'
import { useProjectStore } from './stores/projectStore'
import { usePipelineStore } from './stores/pipelineStore'
import { useToast } from './components/ui/Toast'
import { createProject, listProjects, updateProject } from './api/projects'
import { getUpload } from './api/upload'
import { scanMaterials } from './api/materials'
import { getAnalysis } from './api/analysis'
import AppShell from './components/layout/AppShell'
import ExampleGallery from './components/layout/ExampleGallery'
import UsageDashboardPage from './components/dashboard/UsageDashboardPage'
import VideoUploader from './components/upload/VideoUploader'
import AnalysisPanel from './components/analysis/AnalysisPanel'
import MaterialBrowser from './components/materials/MaterialBrowser'
import PromptWorkspace from './components/prompt/PromptWorkspace'
import GenerationPanel from './components/generation/GenerationPanel'
import TimelineEditor from './components/timeline/TimelineEditor'
import AutoModeStudio from './components/pipeline/AutoModeStudio'
import type { Project, VideoUpload, VideoAnalysis } from './types'
import { Plus, FolderOpen, Film } from 'lucide-react'

function ProjectList({ onSelect }: { onSelect: (p: Project) => void }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const { toast } = useToast()

  useEffect(() => {
    listProjects().then(setProjects).catch(() => toast('error', '加载项目列表失败'))
    scanMaterials().catch(() => {})  // scanning failure is non-critical
  }, [])

  const handleCreate = async () => {
    if (!name.trim()) return
    setCreating(true)
    const p = await createProject(name.trim())
    onSelect(p)
    setCreating(false)
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="max-w-lg w-full mx-4">
        <div className="text-center mb-8">
          <Film className="mx-auto text-gray-700 mb-4" size={64} />
          <h1 className="text-3xl font-bold text-gray-900 mb-2">VidGen</h1>
          <p className="text-gray-500">AI 视频生成工作流平台</p>
        </div>

        <div className="bg-white rounded-2xl p-6 mb-6 shadow-sm border border-gray-200">
          <h2 className="text-gray-900 font-medium mb-4">创建新项目</h2>
          <div className="flex gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              placeholder="项目名称..."
              className="flex-1 bg-gray-50 text-gray-900 rounded-lg px-4 py-2.5 text-sm border border-gray-300 focus:border-blue-500 focus:outline-none"
            />
            <button
              onClick={handleCreate}
              disabled={!name.trim() || creating}
              className="px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg flex items-center gap-2 disabled:opacity-50"
            >
              <Plus size={18} />
              创建
            </button>
          </div>
        </div>

        {projects.length > 0 && (
          <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-200">
            <h2 className="text-gray-900 font-medium mb-4">最近项目</h2>
            <div className="space-y-2">
              {projects.map((p) => (
                <button
                  key={p.id}
                  onClick={() => onSelect(p)}
                  className="w-full text-left px-4 py-3 bg-gray-50 hover:bg-gray-100 rounded-lg flex items-center justify-between transition-colors"
                >
                  <div>
                    <div className="text-gray-900 text-sm">{p.name}</div>
                    <div className="text-xs text-gray-500">步骤 {p.current_step}/7 | {new Date(p.created_at).toLocaleDateString()}</div>
                  </div>
                  <FolderOpen className="text-gray-400" size={18} />
                </button>
              ))}
            </div>
          </div>
        )}

        <ExampleGallery />
      </div>
    </div>
  )
}

export default function App() {
  const { project, setProject, currentStep, setCurrentStep } = useProjectStore()
  const { isAutoMode, setAutoMode, reset: resetPipeline } = usePipelineStore()
  const { toast } = useToast()
  const [showDashboard, setShowDashboard] = useState(false)
  const [upload, setUpload] = useState<VideoUpload | null>(null)
  const [analysis, setAnalysis] = useState<VideoAnalysis | null>(null)
  const [maxStep, setMaxStep] = useState(1)

  useEffect(() => {
    if (!project) return
    setMaxStep(Math.max(project.current_step, 1))
    getUpload(project.id).then(setUpload).catch(() => toast('warning', '加载上传记录失败'))
    getAnalysis(project.id).then(setAnalysis).catch(() => {})  // analysis may not exist yet
  }, [project])

  const goToStep = useCallback((step: number) => {
    setCurrentStep(step)
    if (project && step > maxStep) {
      setMaxStep(step)
      updateProject(project.id, { current_step: step })
    }
  }, [project, maxStep, setCurrentStep])

  const handleNext = useCallback(() => {
    goToStep(currentStep + 1)
  }, [currentStep, goToStep])

  if (!project) {
    return <ProjectList onSelect={(p) => { setProject(p); setCurrentStep(p.current_step); setAutoMode(true) }} />
  }

  return (
    <AppShell
      projectName={project.name}
      currentStep={isAutoMode ? 0 : currentStep}
      maxStep={Math.max(maxStep, currentStep)}
      onStepClick={isAutoMode ? undefined : goToStep}
      onOpenDashboard={() => setShowDashboard(true)}
    >
      <div className="h-full flex flex-col">
        <div className="flex-1 overflow-auto">
          {showDashboard ? (
            <UsageDashboardPage
              currentProjectId={project.id}
              onBack={() => setShowDashboard(false)}
            />
          ) : isAutoMode ? (
            <AutoModeStudio
              projectId={project.id}
              onSwitchToManual={() => {
                setAutoMode(false)
                resetPipeline()
              }}
            />
          ) : (
            /* ── Manual mode: Step-by-step workflow ── */
            <>
              <div className="px-6 py-3 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-gray-900">手动模式流水线</div>
                  <div className="text-xs text-gray-500 mt-0.5">逐步执行上传、分析、选素材、生成和剪辑</div>
                </div>
                <button
                  onClick={() => {
                    setAutoMode(true)
                    resetPipeline()
                  }}
                  className="px-4 py-2 text-sm rounded-lg bg-white border border-gray-300 text-gray-700 hover:bg-gray-100 transition-colors"
                >
                  返回一键生成
                </button>
              </div>
              {currentStep === 1 && (
                <VideoUploader
                  projectId={project.id}
                  upload={upload}
                  onUploaded={(u) => { setUpload(u); setMaxStep(Math.max(maxStep, 2)) }}
                />
              )}
              {currentStep === 2 && (
                <AnalysisPanel
                  projectId={project.id}
                  upload={upload}
                  onComplete={(a) => { setAnalysis(a); setMaxStep(Math.max(maxStep, 3)) }}
                />
              )}
              {currentStep === 3 && (
                <MaterialBrowser
                  projectId={project.id}
                  recommendedCategories={analysis?.recommended_categories || undefined}
                />
              )}
              {currentStep === 4 && (
                <PromptWorkspace projectId={project.id} />
              )}
              {(currentStep === 5 || currentStep === 6) && (
                <GenerationPanel projectId={project.id} />
              )}
              {currentStep === 7 && (
                <TimelineEditor projectId={project.id} />
              )}
            </>
          )}
        </div>

        {/* Step navigation — only in manual mode */}
        {!isAutoMode && !showDashboard && (
          <div className="flex items-center justify-between px-6 py-3 bg-white border-t border-gray-200">
            <button
              onClick={() => currentStep === 1 ? setProject(null) : goToStep(currentStep - 1)}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
            >
              {currentStep === 1 ? '返回项目列表' : '上一步'}
            </button>
            {currentStep < 7 && (
              <button
                onClick={handleNext}
                className="px-6 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
              >
                下一步
              </button>
            )}
          </div>
        )}
      </div>
    </AppShell>
  )
}
