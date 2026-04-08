import { useState, useEffect, useCallback, useRef } from 'react'
import { useProjectStore } from './stores/projectStore'
import { usePipelineStore } from './stores/pipelineStore'
import { useToast } from './components/ui/Toast'
import { getMe, logout } from './api/auth'
import { createProject, listProjects, updateProject } from './api/projects'
import { getUpload } from './api/upload'
import { scanMaterials } from './api/materials'
import { getAnalysis } from './api/analysis'
import AppShell from './components/layout/AppShell'
import AuthPage from './components/auth/AuthPage'
import ExampleGallery from './components/layout/ExampleGallery'
import UsageDashboardPage from './components/dashboard/UsageDashboardPage'
import VideoUploader from './components/upload/VideoUploader'
import AnalysisPanel from './components/analysis/AnalysisPanel'
import MaterialBrowser from './components/materials/MaterialBrowser'
import PromptWorkspace from './components/prompt/PromptWorkspace'
import GenerationPanel from './components/generation/GenerationPanel'
import TimelineEditor from './components/timeline/TimelineEditor'
import AutoModeStudio from './components/pipeline/AutoModeStudio'
import RepositoryPage from './components/repository/RepositoryPage'
import type { AuthUser, Project, VideoUpload, VideoAnalysis } from './types'
import { Plus, FolderOpen, Wand2 } from 'lucide-react'
import CapyAvatar from './components/ui/CapyAvatar'

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
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(169,190,120,0.22),_transparent_30%),linear-gradient(180deg,#f8f0e1_0%,#eee2ca_100%)] flex items-center justify-center">
      <div className="max-w-lg w-full mx-4">
        <div className="text-center mb-8">
          <CapyAvatar size="lg" className="mx-auto mb-4 border-[#ccb98f] bg-[#faf1de]" />
          <h1 className="text-3xl font-bold text-[#4c3b22] mb-2">capy</h1>
          <p className="text-[#7b6847]">像卡皮巴拉一样稳定推进的视频工作台</p>
        </div>

        <div className="rounded-[28px] border border-[#d7c7a8] bg-white/82 p-6 mb-6 shadow-sm backdrop-blur">
          <h2 className="text-[#4c3b22] font-medium mb-4">创建新项目</h2>
          <div className="flex gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              placeholder="项目名称..."
              className="flex-1 rounded-2xl border border-[#daccb3] bg-[#fff8ec] px-4 py-2.5 text-sm text-[#4c3b22] focus:border-[#8ca65c] focus:outline-none"
            />
            <button
              onClick={handleCreate}
              disabled={!name.trim() || creating}
              className="px-4 py-2.5 bg-[#7e9d53] hover:bg-[#718f47] text-white rounded-2xl flex items-center gap-2 disabled:opacity-50"
            >
              <Plus size={18} />
              创建
            </button>
          </div>
        </div>

        {projects.length > 0 && (
          <div className="rounded-[28px] border border-[#d7c7a8] bg-white/82 p-6 shadow-sm backdrop-blur">
            <h2 className="text-[#4c3b22] font-medium mb-4">最近项目</h2>
            <div className="space-y-2">
              {projects.map((p) => (
                <button
                  key={p.id}
                  onClick={() => onSelect(p)}
                  className="w-full text-left px-4 py-3 bg-[#fff8ed] hover:bg-[#f7ecd7] rounded-2xl flex items-center justify-between transition-colors border border-[#e6dbc8]"
                >
                  <div>
                    <div className="text-[#4c3b22] text-sm">{p.name}</div>
                    <div className="text-xs text-[#8a7857]">步骤 {p.current_step}/7 | {new Date(p.created_at).toLocaleDateString()}</div>
                  </div>
                  <FolderOpen className="text-[#9a845d]" size={18} />
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
  const openPickerFnRef = useRef<(() => void) | null>(null)
  const repositoryPickerCallbackRef = useRef<((items: import('./types').MaterialItem[]) => void) | null>(null)
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [showDashboard, setShowDashboard] = useState(false)
  const [showRepository, setShowRepository] = useState(false)
  const [upload, setUpload] = useState<VideoUpload | null>(null)
  const [analysis, setAnalysis] = useState<VideoAnalysis | null>(null)
  const [maxStep, setMaxStep] = useState(1)

  useEffect(() => {
    getMe().then(setAuthUser).catch(() => setAuthUser(null)).finally(() => setAuthLoading(false))
  }, [])

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

  const handleLogout = useCallback(async () => {
    try {
      await logout()
    } catch {}
    setAuthUser(null)
    setProject(null)
    resetPipeline()
    setShowDashboard(false)
    setShowRepository(false)
  }, [resetPipeline, setProject])

  const handleSwitchProject = useCallback((p: Project) => {
    setProject(p)
    setCurrentStep(p.current_step)
    setAutoMode(true)
    resetPipeline()
    setShowDashboard(false)
    setShowRepository(false)
  }, [setProject, setCurrentStep, setAutoMode, resetPipeline])

  if (authLoading) {
    return <div className="min-h-screen flex items-center justify-center text-[#7c6845]">加载登录状态中...</div>
  }

  if (!authUser) {
    return <AuthPage onAuthenticated={setAuthUser} />
  }

  if (!project) {
    return <ProjectList onSelect={(p) => { setProject(p); setCurrentStep(p.current_step); setAutoMode(true) }} />
  }

  return (
    <AppShell
      projectName={project.name}
      currentUserName={authUser.username}
      currentStep={isAutoMode ? 0 : currentStep}
      maxStep={Math.max(maxStep, currentStep)}
      onStepClick={isAutoMode ? undefined : goToStep}
      onOpenDashboard={() => { setShowDashboard(true); setShowRepository(false) }}
      onOpenRepository={() => { setShowRepository(true); setShowDashboard(false) }}
      onLogout={handleLogout}
      centerSlot={isAutoMode ? (
        <div className="flex items-center gap-2">
          <button className="rounded-full bg-[#7e9d53] text-white px-4 py-2 text-sm font-medium flex items-center gap-2">
            <Wand2 size={14} /> 一键生成
          </button>
          <button
            onClick={() => { setAutoMode(false); resetPipeline() }}
            className="rounded-full bg-[#fffaf1] border border-[#d9ccb5] text-[#6d5936] px-4 py-2 text-sm font-medium hover:bg-[#f7ecd8]"
          >
            手动模式
          </button>
        </div>
      ) : undefined}
    >
      <div className="h-full flex flex-col">
        <div className="flex-1 overflow-auto">
          {showRepository ? (
            <RepositoryPage
              onBack={() => { setShowRepository(false); repositoryPickerCallbackRef.current = null }}
              onPickerConfirm={repositoryPickerCallbackRef.current ? (items) => {
                repositoryPickerCallbackRef.current!(items)
                repositoryPickerCallbackRef.current = null
                setShowRepository(false)
              } : undefined}
            />
          ) : showDashboard ? (
            <UsageDashboardPage
              currentProjectId={project.id}
              currentUser={authUser}
              onBack={() => setShowDashboard(false)}
            />
          ) : isAutoMode ? (
            <AutoModeStudio
              projectId={project.id}
              onSwitchToManual={() => { setAutoMode(false); resetPipeline() }}
              onSwitchProject={handleSwitchProject}
              onRegisterOpenPicker={(fn) => { openPickerFnRef.current = fn }}
              onOpenRepositoryWithPicker={(cb) => { repositoryPickerCallbackRef.current = cb; setShowRepository(true); setShowDashboard(false) }}
            />
          ) : (
            /* ── Manual mode: Step-by-step workflow ── */
            <>
              <div className="px-6 py-3 border-b border-[#d8c9ad] bg-[#f8f0e1] flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-[#4c3b22]">手动模式流水线</div>
                  <div className="text-xs text-[#867351] mt-0.5">逐步执行上传、分析、选素材、生成和剪辑</div>
                </div>
                <button
                  onClick={() => {
                    setAutoMode(true)
                    resetPipeline()
                  }}
                  className="px-4 py-2 text-sm rounded-2xl bg-[#fff8ec] border border-[#d7c7a8] text-[#6d5936] hover:bg-[#f5ebd7] transition-colors"
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
        {!isAutoMode && !showDashboard && !showRepository && (
          <div className="flex items-center justify-between px-6 py-3 bg-[#fff9ef] border-t border-[#d8c9ad]">
            <button
              onClick={() => currentStep === 1 ? setProject(null) : goToStep(currentStep - 1)}
              className="px-4 py-2 text-sm text-[#6f5b38] hover:text-[#4c3b22] bg-[#f2e8d6] hover:bg-[#e8dcc4] rounded-2xl transition-colors"
            >
              {currentStep === 1 ? '返回项目列表' : '上一步'}
            </button>
            {currentStep < 7 && (
              <button
                onClick={handleNext}
                className="px-6 py-2 text-sm bg-[#7e9d53] hover:bg-[#718f47] text-white rounded-2xl transition-colors"
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
