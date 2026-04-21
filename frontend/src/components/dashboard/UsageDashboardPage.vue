<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { deleteProject, getProjectHistory, getProjectUsage, listProjects } from '../../api/projects'
import { listUsers, updateUser } from '../../api/auth'
import {
  createBackgroundTemplate,
  deleteBackgroundTemplate,
  generateBackgroundTemplateFromKeywords,
  importPresetBackgroundTemplates,
  listBackgroundTemplates,
  updateBackgroundTemplate,
} from '../../api/backgroundTemplates'
import type {
  AuthUser,
  BackgroundTemplate,
  BackgroundTemplateKeywordDraft,
  Project,
  ProjectHistoryRun,
  ProjectUsageSummary,
} from '../../types'
import { toast } from '../../composables/useToast'

const props = defineProps<{
  currentProjectId: string
  currentUser: AuthUser
}>()

const emit = defineEmits<{
  back: []
}>()

const tab = ref<'dashboard' | 'profile'>('dashboard')
const projects = ref<Project[]>([])
const selectedProjectId = ref<string | null>(props.currentProjectId)
const summary = ref<ProjectUsageSummary | null>(null)
const historyRuns = ref<ProjectHistoryRun[]>([])
const users = ref<AuthUser[]>([])
const loading = ref(false)
const deletingProjectId = ref<string | null>(null)
const templates = ref<BackgroundTemplate[]>([])
const selectedTemplateId = ref<string | null>(null)
const templateDraft = ref<Partial<BackgroundTemplate>>({ name: '' })
const keywords = ref('')
const generatingTemplate = ref(false)
const savingTemplate = ref(false)

const selectedProject = computed(() => projects.value.find((project) => project.id === selectedProjectId.value) || null)
const selectedTemplate = computed(() => templates.value.find((template) => template.id === selectedTemplateId.value) || null)

const templateFields: Array<{ key: keyof BackgroundTemplateKeywordDraft; label: string; rows?: number }> = [
  { key: 'brand_info', label: '品牌信息', rows: 2 },
  { key: 'user_requirements', label: '用户要求', rows: 2 },
  { key: 'character_name', label: '角色名称' },
  { key: 'identity', label: '角色身份', rows: 2 },
  { key: 'scene_context', label: '场景语境', rows: 2 },
  { key: 'tone_style', label: '语气风格', rows: 2 },
  { key: 'visual_style', label: '视觉风格', rows: 2 },
  { key: 'do_not_include', label: '禁止内容', rows: 2 },
  { key: 'notes', label: '备注', rows: 2 },
]

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : '-'
}

async function refreshProjects() {
  projects.value = await listProjects().catch(() => [])
}

async function refreshUsage() {
  if (!selectedProjectId.value) return
  loading.value = true
  try {
    const [usage, history] = await Promise.all([
      getProjectUsage(selectedProjectId.value),
      getProjectHistory(selectedProjectId.value),
    ])
    summary.value = usage
    historyRuns.value = history.runs
  } catch {
    toast('error', '加载仪表盘失败')
  } finally {
    loading.value = false
  }
}

async function refreshUsers() {
  if (props.currentUser.role !== 'admin') return
  users.value = await listUsers().catch(() => [])
}

function editTemplate(template: BackgroundTemplate | null) {
  selectedTemplateId.value = template?.id || null
  templateDraft.value = template ? { ...template } : { name: '' }
}

async function refreshTemplates() {
  templates.value = await listBackgroundTemplates().catch(() => [])
  if (templates.value.length > 0 && !selectedTemplateId.value) editTemplate(templates.value[0])
}

async function toggleUser(user: AuthUser) {
  try {
    const updated = await updateUser(user.id, { is_active: !user.is_active })
    users.value = users.value.map((item) => (item.id === updated.id ? updated : item))
  } catch {
    toast('error', '更新用户失败')
  }
}

async function removeProject(project: Project) {
  deletingProjectId.value = project.id
  try {
    await deleteProject(project.id)
    projects.value = projects.value.filter((item) => item.id !== project.id)
    if (selectedProjectId.value === project.id) {
      selectedProjectId.value = projects.value[0]?.id || null
    }
    toast('success', '项目已删除')
  } catch {
    toast('error', '删除项目失败')
  } finally {
    deletingProjectId.value = null
  }
}

async function saveTemplate() {
  if (!templateDraft.value.name?.trim()) {
    toast('warning', '请填写模板名称')
    return
  }
  savingTemplate.value = true
  try {
    const payload = { ...templateDraft.value, name: templateDraft.value.name.trim() }
    const saved = selectedTemplateId.value
      ? await updateBackgroundTemplate(selectedTemplateId.value, payload)
      : await createBackgroundTemplate(payload)
    await refreshTemplates()
    editTemplate(saved)
    toast('success', '背景模板已保存')
  } catch {
    toast('error', '保存背景模板失败')
  } finally {
    savingTemplate.value = false
  }
}

async function removeTemplate() {
  if (!selectedTemplateId.value) return
  try {
    await deleteBackgroundTemplate(selectedTemplateId.value)
    selectedTemplateId.value = null
    templateDraft.value = { name: '' }
    await refreshTemplates()
    toast('success', '背景模板已删除')
  } catch {
    toast('error', '删除背景模板失败')
  }
}

async function importPresets() {
  try {
    await importPresetBackgroundTemplates()
    await refreshTemplates()
    toast('success', '预设模板已导入')
  } catch {
    toast('error', '导入预设模板失败')
  }
}

async function generateTemplateDraft() {
  if (!keywords.value.trim()) return
  generatingTemplate.value = true
  try {
    const draft = await generateBackgroundTemplateFromKeywords({
      keywords: keywords.value.trim(),
      template_id: selectedTemplateId.value,
    })
    templateDraft.value = { ...templateDraft.value, ...draft }
    toast('success', '已根据关键词生成草稿')
  } catch {
    toast('error', '生成模板草稿失败')
  } finally {
    generatingTemplate.value = false
  }
}

onMounted(async () => {
  await refreshProjects()
  await Promise.all([refreshUsage(), refreshUsers(), refreshTemplates()])
})
watch(selectedProjectId, refreshUsage)
</script>

<template>
  <section class="flex h-full flex-col overflow-hidden bg-[#f8f0e1]">
    <header class="flex items-center justify-between border-b border-[#d8c9ad] bg-[#fff8ec] px-6 py-4">
      <div class="flex items-center gap-3">
        <button type="button" class="rounded-lg border border-[#d7c7a8] px-3 py-2 text-sm hover:bg-[#f4ead8]" @click="emit('back')">
          返回
        </button>
        <div>
          <h2 class="text-lg font-semibold">仪表盘</h2>
          <p class="text-sm text-[#867351]">项目消耗、历史产物和个人设置。</p>
        </div>
      </div>
      <div class="rounded-lg bg-[#f2e8d6] p-1">
        <button type="button" class="rounded-md px-3 py-2 text-sm" :class="tab === 'dashboard' ? 'bg-white shadow-sm' : ''" @click="tab = 'dashboard'">
          项目
        </button>
        <button type="button" class="rounded-md px-3 py-2 text-sm" :class="tab === 'profile' ? 'bg-white shadow-sm' : ''" @click="tab = 'profile'">
          个人中心
        </button>
      </div>
    </header>

    <main class="min-h-0 flex-1 overflow-auto p-6">
      <template v-if="tab === 'dashboard'">
        <div class="mb-5 flex flex-wrap items-center justify-between gap-3">
          <select v-model="selectedProjectId" class="rounded-lg border border-[#d7c7a8] bg-white px-4 py-2 text-sm">
            <option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option>
          </select>
          <button
            v-if="selectedProject"
            type="button"
            class="rounded-lg border border-[#d7c7a8] px-3 py-2 text-sm hover:bg-[#f4ead8] disabled:opacity-50"
            :disabled="deletingProjectId === selectedProject.id"
            @click="removeProject(selectedProject)"
          >
            {{ deletingProjectId === selectedProject.id ? '删除中...' : '删除当前项目' }}
          </button>
        </div>

        <div v-if="loading" class="rounded-lg border border-[#d7c7a8] bg-white/75 p-5 text-sm text-[#867351]">加载中...</div>
        <template v-else>
          <div class="mb-6 grid gap-4 md:grid-cols-4">
            <article class="rounded-lg border border-[#d7c7a8] bg-white/85 p-4">
              <div class="text-xs text-[#867351]">Prompt Tokens</div>
              <div class="mt-2 text-2xl font-semibold">{{ summary?.prompt_tokens || 0 }}</div>
            </article>
            <article class="rounded-lg border border-[#d7c7a8] bg-white/85 p-4">
              <div class="text-xs text-[#867351]">Completion Tokens</div>
              <div class="mt-2 text-2xl font-semibold">{{ summary?.completion_tokens || 0 }}</div>
            </article>
            <article class="rounded-lg border border-[#d7c7a8] bg-white/85 p-4">
              <div class="text-xs text-[#867351]">Total Tokens</div>
              <div class="mt-2 text-2xl font-semibold">{{ summary?.total_tokens || 0 }}</div>
            </article>
            <article class="rounded-lg border border-[#d7c7a8] bg-white/85 p-4">
              <div class="text-xs text-[#867351]">Requests</div>
              <div class="mt-2 text-2xl font-semibold">{{ summary?.request_count || 0 }}</div>
            </article>
          </div>

          <section class="rounded-lg border border-[#d7c7a8] bg-white/85">
            <div class="border-b border-[#eadfca] px-4 py-3 font-semibold">历史运行</div>
            <div v-if="historyRuns.length === 0" class="p-5 text-sm text-[#867351]">暂无历史运行。</div>
            <article v-for="run in historyRuns" :key="run.run_id" class="border-b border-[#eadfca] p-4 last:border-0">
              <div class="mb-3 flex items-center justify-between gap-3">
                <div>
                  <div class="font-medium">{{ run.status }} · {{ run.current_agent || '完成' }}</div>
                  <div class="mt-1 text-xs text-[#8a7857]">{{ formatDate(run.created_at) }} - {{ formatDate(run.completed_at) }}</div>
                </div>
                <div class="text-right text-xs text-[#8a7857]">{{ run.total_tokens }} tokens · {{ run.request_count }} requests</div>
              </div>
              <p v-if="run.input_script" class="line-clamp-3 text-sm leading-6 text-[#6d5936]">{{ run.input_script }}</p>
              <div v-if="run.final_videos.length" class="mt-3 grid gap-3 md:grid-cols-2">
                <a
                  v-for="video in run.final_videos"
                  :key="video.path"
                  :href="video.url"
                  target="_blank"
                  class="rounded-lg border border-[#d7c7a8] bg-[#fff8ec] p-3 text-sm hover:bg-[#f4ead8]"
                >
                  {{ video.name }}
                </a>
              </div>
            </article>
          </section>
        </template>
      </template>

      <template v-else>
        <section class="mb-6 rounded-lg border border-[#d7c7a8] bg-white/85 p-5">
          <h3 class="mb-3 font-semibold">当前账号</h3>
          <div class="grid gap-3 text-sm md:grid-cols-2">
            <div>用户名：{{ currentUser.username }}</div>
            <div>角色：{{ currentUser.role }}</div>
            <div>状态：{{ currentUser.is_active ? '启用' : '停用' }}</div>
            <div>创建时间：{{ formatDate(currentUser.created_at) }}</div>
          </div>
        </section>

        <section class="mb-6 grid gap-4 lg:grid-cols-[280px_1fr]">
          <aside class="rounded-lg border border-[#d7c7a8] bg-white/85 p-4">
            <div class="mb-3 flex items-center justify-between">
              <h3 class="font-semibold">角色背景模板</h3>
              <button type="button" class="rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8]" @click="editTemplate(null)">
                新建
              </button>
            </div>
            <div class="mb-3 flex gap-2">
              <button type="button" class="rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8]" @click="importPresets">
                导入预设
              </button>
            </div>
            <div class="space-y-2">
              <button
                v-for="template in templates"
                :key="template.id"
                type="button"
                class="w-full rounded-lg p-3 text-left text-sm"
                :class="selectedTemplateId === template.id ? 'bg-[#7e9d53] text-white' : 'bg-[#fff8ec] text-[#6d5936] hover:bg-[#f4ead8]'"
                @click="editTemplate(template)"
              >
                <div class="truncate font-medium">{{ template.name }}</div>
                <div class="mt-1 text-xs opacity-75">学习 {{ template.learning_count }} 次</div>
              </button>
            </div>
          </aside>

          <div class="rounded-lg border border-[#d7c7a8] bg-white/85 p-5">
            <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 class="font-semibold">{{ selectedTemplate ? '编辑模板' : '新建模板' }}</h3>
                <p class="text-sm text-[#867351]">这些背景信息会在自动模式中作为角色语境使用。</p>
              </div>
              <div class="flex gap-2">
                <button
                  v-if="selectedTemplateId"
                  type="button"
                  class="rounded-lg border border-[#d7c7a8] px-3 py-2 text-xs hover:bg-[#f4ead8]"
                  @click="removeTemplate"
                >
                  删除
                </button>
                <button
                  type="button"
                  class="rounded-lg bg-[#7e9d53] px-3 py-2 text-xs text-white disabled:opacity-50"
                  :disabled="savingTemplate"
                  @click="saveTemplate"
                >
                  {{ savingTemplate ? '保存中...' : '保存模板' }}
                </button>
              </div>
            </div>

            <label class="mb-4 block">
              <span class="mb-1 block text-xs text-[#867351]">模板名称</span>
              <input v-model="templateDraft.name" class="w-full rounded-lg border border-[#d7c7a8] bg-white px-3 py-2 text-sm">
            </label>

            <div class="mb-4 rounded-lg border border-[#eadfca] bg-[#fff8ec] p-3">
              <label class="block">
                <span class="mb-1 block text-xs text-[#867351]">关键词生成</span>
                <textarea v-model="keywords" class="min-h-20 w-full rounded-lg border border-[#d7c7a8] bg-white p-3 text-sm" placeholder="例如：母婴品牌、温柔可信、适合抖音口播、避免夸张承诺" />
              </label>
              <button
                type="button"
                class="mt-2 rounded-lg border border-[#d7c7a8] px-3 py-2 text-xs hover:bg-white disabled:opacity-50"
                :disabled="generatingTemplate || !keywords.trim()"
                @click="generateTemplateDraft"
              >
                {{ generatingTemplate ? '生成中...' : '生成草稿' }}
              </button>
            </div>

            <div class="grid gap-3 md:grid-cols-2">
              <label v-for="field in templateFields" :key="field.key" class="block">
                <span class="mb-1 block text-xs text-[#867351]">{{ field.label }}</span>
                <textarea
                  v-model="templateDraft[field.key]"
                  class="w-full rounded-lg border border-[#d7c7a8] bg-white p-3 text-sm"
                  :rows="field.rows || 1"
                />
              </label>
            </div>
          </div>
        </section>

        <section v-if="currentUser.role === 'admin'" class="rounded-lg border border-[#d7c7a8] bg-white/85">
          <div class="border-b border-[#eadfca] px-4 py-3 font-semibold">用户管理</div>
          <article v-for="user in users" :key="user.id" class="flex items-center justify-between border-b border-[#eadfca] p-4 last:border-0">
            <div>
              <div class="font-medium">{{ user.username }}</div>
              <div class="text-xs text-[#8a7857]">{{ user.role }} · {{ user.is_active ? '启用' : '停用' }}</div>
            </div>
            <button type="button" class="rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8]" @click="toggleUser(user)">
              {{ user.is_active ? '停用' : '启用' }}
            </button>
          </article>
        </section>
      </template>
    </main>
  </section>
</template>
