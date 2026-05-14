<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { getMe, logout } from './api/auth'
import { createProject, getProject, listProjects, updateProject } from './api/projects'
import { getUpload } from './api/upload'
import { getAnalysis } from './api/analysis'
import { scanMaterials } from './api/materials'
import { projectStore, setCurrentStep, setProject } from './stores/projectStore'
import { pipelineStore, resetPipeline, setAutoMode } from './stores/pipelineStore'
import type {
  AuthUser,
  MaterialItem,
  Project,
  RepositoryDelivery,
  RepositoryUpload,
  VideoAnalysis,
  VideoUpload,
} from './types'
import { toast } from './composables/useToast'
import ToastHost from './components/ui/ToastHost.vue'
import CapyAvatar from './components/ui/CapyAvatar.vue'
import AppShell from './components/layout/AppShell.vue'
import ExampleGallery from './components/layout/ExampleGallery.vue'
import AuthPage from './components/auth/AuthPage.vue'
import VideoUploader from './components/upload/VideoUploader.vue'
import AnalysisPanel from './components/analysis/AnalysisPanel.vue'
import MaterialBrowser from './components/materials/MaterialBrowser.vue'
import PromptWorkspace from './components/prompt/PromptWorkspace.vue'
import GenerationPanel from './components/generation/GenerationPanel.vue'
import TimelineEditor from './components/timeline/TimelineEditor.vue'
import AutoModeStudio from './components/pipeline/AutoModeStudio.vue'
import RepositoryPage from './components/repository/RepositoryPage.vue'
import UsageDashboardPage from './components/dashboard/UsageDashboardPage.vue'

type AutoModeStudioExpose = {
  handleRepositoryPicked: (
    items: MaterialItem[],
    uploads: RepositoryUpload[],
    delivery: RepositoryDelivery | null,
  ) => Promise<void>
}

const authUser = ref<AuthUser | null>(null)
const authLoading = ref(true)
const projects = ref<Project[]>([])
const creatingProject = ref(false)
const projectName = ref('')
const showDashboard = ref(false)
const showRepository = ref(false)
const repositoryPickerMode = ref(false)
const openingRepository = ref(false)
const upload = ref<VideoUpload | null>(null)
const analysis = ref<VideoAnalysis | null>(null)
const maxStep = ref(1)
const autoModeStudio = ref<AutoModeStudioExpose | null>(null)

const project = computed(() => projectStore.project)
const currentStep = computed(() => projectStore.currentStep)
const isAutoMode = computed(() => pipelineStore.isAutoMode)

async function loadAuth() {
  try {
    authUser.value = await getMe()
    await loadProjects()
  } catch {
    authUser.value = null
  } finally {
    authLoading.value = false
  }
}

async function loadProjects() {
  projects.value = await listProjects().catch(() => [])
  scanMaterials().catch(() => undefined)
}

async function createNewProject() {
  const name = projectName.value.trim()
  if (!name || creatingProject.value) return

  creatingProject.value = true
  try {
    const created = await createProject(name)
    projects.value.unshift(created)
    selectProject(created)
    projectName.value = ''
  } catch {
    toast('error', '创建项目失败')
  } finally {
    creatingProject.value = false
  }
}

function selectProject(nextProject: Project) {
  setProject(nextProject)
  setCurrentStep(nextProject.current_step)
  setAutoMode(true)
  resetPipeline()
  showDashboard.value = false
  showRepository.value = false
  repositoryPickerMode.value = false
}

async function loadProjectData() {
  if (!project.value) return
  maxStep.value = Math.max(project.value.current_step, 1)
  upload.value = await getUpload(project.value.id).catch(() => null)
  analysis.value = await getAnalysis(project.value.id).catch(() => null)
}

function goToStep(step: number) {
  if (!project.value) return
  setCurrentStep(step)
  if (step > maxStep.value) {
    maxStep.value = step
    updateProject(project.value.id, { current_step: step }).catch(() => undefined)
  }
}

function handleNext() {
  goToStep(currentStep.value + 1)
}

async function handleLogout() {
  try {
    await logout()
  } catch {
    // The local session state still needs clearing even if the backend cookie is gone.
  }
  authUser.value = null
  setProject(null)
  resetPipeline()
  showDashboard.value = false
  showRepository.value = false
  repositoryPickerMode.value = false
}

async function ensureCurrentProject() {
  const current = project.value
  if (!current?.id) return false

  try {
    const freshProject = await getProject(current.id)
    setProject(freshProject)
    return true
  } catch {
    const freshProjects = await listProjects().catch(() => [])
    projects.value = freshProjects
    const fallbackProject = freshProjects[0] || null
    if (fallbackProject) {
      selectProject(fallbackProject)
      toast('warning', '当前项目不存在，已切换到最近项目')
      return true
    }
    setProject(null)
    toast('warning', '当前项目不存在，请先创建新项目')
    return false
  }
}

async function openRepository(pickerMode = false) {
  if (openingRepository.value) return
  openingRepository.value = true
  const canOpen = await ensureCurrentProject().finally(() => {
    openingRepository.value = false
  })
  if (!canOpen) return

  repositoryPickerMode.value = pickerMode
  showRepository.value = true
  showDashboard.value = false
}

async function handleRepositoryConfirm(
  items: MaterialItem[],
  selectedUploads: RepositoryUpload[],
  selectedDelivery: RepositoryDelivery | null,
) {
  try {
    await autoModeStudio.value?.handleRepositoryPicked(items, selectedUploads, selectedDelivery)
    showRepository.value = false
    repositoryPickerMode.value = false
  } catch {
    // Keep picker open when applying repository selection fails.
  }
}

function backFromRepository() {
  showRepository.value = false
  repositoryPickerMode.value = false
}

function switchToManual(step = 7) {
  setAutoMode(false)
  resetPipeline()
  goToStep(step)
}

function switchToAuto() {
  setAutoMode(true)
  resetPipeline()
}

onMounted(loadAuth)
watch(project, loadProjectData)
</script>

<template>
  <ToastHost />

  <div v-if="authLoading" class="flex min-h-screen items-center justify-center text-[#7c6845]">
    加载登录状态中...
  </div>

  <AuthPage v-else-if="!authUser" @authenticated="authUser = $event; loadProjects()" />

  <section
    v-else-if="!project"
    class="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_top,_rgba(169,190,120,0.22),_transparent_30%),linear-gradient(180deg,#f8f0e1_0%,#eee2ca_100%)] px-4"
  >
    <div class="w-full max-w-lg">
      <div class="mb-8 text-center">
        <CapyAvatar size="lg" className="mx-auto mb-4 border-[#ccb98f] bg-[#faf1de]" />
        <h1 class="mb-2 text-3xl font-bold text-[#4c3b22]">capy</h1>
        <p class="text-[#7b6847]">像卡皮巴拉一样稳定推进的视频工作台</p>
      </div>

      <form class="mb-6 rounded-lg border border-[#d7c7a8] bg-white/82 p-6 shadow-sm backdrop-blur" @submit.prevent="createNewProject">
        <h2 class="mb-4 font-medium text-[#4c3b22]">创建新项目</h2>
        <div class="flex gap-2">
          <input
            v-model="projectName"
            class="flex-1 rounded-lg border border-[#daccb3] bg-[#fff8ec] px-4 py-2.5 text-sm text-[#4c3b22] outline-none focus:border-[#8ca65c]"
            placeholder="项目名称..."
          >
          <button
            type="submit"
            class="rounded-lg bg-[#7e9d53] px-4 py-2.5 text-sm text-white hover:bg-[#718f47] disabled:opacity-50"
            :disabled="!projectName.trim() || creatingProject"
          >
            {{ creatingProject ? '创建中' : '创建' }}
          </button>
        </div>
      </form>

      <div v-if="projects.length > 0" class="rounded-lg border border-[#d7c7a8] bg-white/82 p-6 shadow-sm backdrop-blur">
        <h2 class="mb-4 font-medium text-[#4c3b22]">最近项目</h2>
        <div class="space-y-2">
          <button
            v-for="item in projects"
            :key="item.id"
            type="button"
            class="flex w-full items-center justify-between rounded-lg border border-[#e6dbc8] bg-[#fff8ed] px-4 py-3 text-left transition-colors hover:bg-[#f7ecd7]"
            @click="selectProject(item)"
          >
            <div>
              <div class="text-sm text-[#4c3b22]">{{ item.name }}</div>
              <div class="text-xs text-[#8a7857]">步骤 {{ item.current_step }}/7 · {{ new Date(item.created_at).toLocaleDateString() }}</div>
            </div>
            <span class="text-[#9a845d]">打开</span>
          </button>
        </div>
      </div>

      <ExampleGallery />
    </div>
  </section>

  <AppShell
    v-else
    :project-name="project.name"
    :current-user-name="authUser.username"
    :current-step="isAutoMode ? 0 : currentStep"
    :max-step="Math.max(maxStep, currentStep)"
    :auto-mode="isAutoMode"
    @step-click="goToStep"
    @open-dashboard="showDashboard = true; showRepository = false"
    @open-repository="openRepository(false)"
    @logout="handleLogout"
  >
    <template #center>
      <div v-if="isAutoMode" class="flex items-center gap-2">
        <button type="button" class="rounded-lg bg-[#7e9d53] px-4 py-2 text-sm font-medium text-white">
          一键生成
        </button>
        <button
          type="button"
          class="rounded-lg border border-[#d9ccb5] bg-[#fffaf1] px-4 py-2 text-sm font-medium text-[#6d5936] hover:bg-[#f7ecd8]"
          @click="switchToManual(7)"
        >
          手动模式
        </button>
      </div>
    </template>

    <div class="flex h-full flex-col">
      <div class="min-h-0 flex-1 overflow-hidden">
        <RepositoryPage
          v-if="showRepository"
          :project-id="project.id"
          :picker-mode="repositoryPickerMode"
          @back="backFromRepository"
          @picker-confirm="handleRepositoryConfirm"
        />
        <UsageDashboardPage
          v-else-if="showDashboard"
          :current-project-id="project.id"
          :current-user="authUser"
          @back="showDashboard = false"
        />
        <AutoModeStudio
          v-show="isAutoMode && !showDashboard && !showRepository"
          ref="autoModeStudio"
          :project-id="project.id"
          @switch-to-manual="switchToManual(7)"
          @switch-project="selectProject"
          @open-repository-picker="openRepository(true)"
        />
        <template v-if="!isAutoMode && !showRepository && !showDashboard">
          <div class="flex items-center justify-between border-b border-[#d8c9ad] bg-[#f8f0e1] px-6 py-3">
            <div>
              <div class="text-sm font-medium text-[#4c3b22]">手动模式流水线</div>
              <div class="mt-0.5 text-xs text-[#867351]">逐步执行上传、分析、选素材、生成和剪辑</div>
            </div>
            <button
              type="button"
              class="rounded-lg border border-[#d7c7a8] bg-[#fff8ec] px-4 py-2 text-sm text-[#6d5936] transition-colors hover:bg-[#f5ebd7]"
              @click="switchToAuto"
            >
              返回一键生成
            </button>
          </div>
          <VideoUploader
            v-if="currentStep === 1"
            :project-id="project.id"
            :upload="upload"
            @uploaded="upload = $event; maxStep = Math.max(maxStep, 2)"
          />
          <AnalysisPanel
            v-else-if="currentStep === 2"
            :project-id="project.id"
            :upload="upload"
            @complete="analysis = $event; maxStep = Math.max(maxStep, 3)"
          />
          <MaterialBrowser
            v-else-if="currentStep === 3"
            :project-id="project.id"
            :recommended-categories="analysis?.recommended_categories || undefined"
          />
          <PromptWorkspace v-else-if="currentStep === 4" :project-id="project.id" />
          <GenerationPanel v-else-if="currentStep === 5 || currentStep === 6" :project-id="project.id" />
          <TimelineEditor v-else-if="currentStep === 7" :project-id="project.id" />
        </template>
      </div>

      <div v-if="!isAutoMode && !showDashboard && !showRepository" class="flex items-center justify-between border-t border-[#d8c9ad] bg-[#fff9ef] px-6 py-3">
        <button
          type="button"
          class="rounded-lg bg-[#f2e8d6] px-4 py-2 text-sm text-[#6f5b38] transition-colors hover:bg-[#e8dcc4] hover:text-[#4c3b22]"
          @click="currentStep === 1 ? setProject(null) : goToStep(currentStep - 1)"
        >
          {{ currentStep === 1 ? '返回项目列表' : '上一步' }}
        </button>
        <button
          v-if="currentStep < 7"
          type="button"
          class="rounded-lg bg-[#7e9d53] px-6 py-2 text-sm text-white transition-colors hover:bg-[#718f47]"
          @click="handleNext"
        >
          下一步
        </button>
      </div>
    </div>
  </AppShell>
</template>
