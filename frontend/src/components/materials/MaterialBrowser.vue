<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  deselectMaterial,
  getCategories,
  getMaterials,
  getSelectedMaterials,
  scanMaterials,
  selectMaterial,
  uploadProjectMaterials,
} from '../../api/materials'
import type { MaterialCategory, MaterialItem, MaterialSelection } from '../../types'
import { toast } from '../../composables/useToast'

const props = defineProps<{
  projectId: string
  recommendedCategories?: string[]
}>()

const categories = ref<MaterialCategory[]>([])
const activeCategory = ref('')
const materials = ref<MaterialItem[]>([])
const selections = ref<MaterialSelection[]>([])
const loading = ref(false)
const uploading = ref(false)
const uploadProgress = ref(0)
const materialFileAccept = 'image/*,audio/mpeg,audio/wav,audio/x-wav,audio/aac,audio/flac,audio/ogg,audio/mp4,audio/webm,.mp3,.wav,.m4a,.aac,.flac,.ogg,.webm'

const selectedIds = computed(() => new Set(selections.value.map((item) => item.material_id)))

function previewUrl(item: MaterialItem) {
  return item.thumbnail_url || `/api/materials/${item.id}/file`
}

function materialFileUrl(item: MaterialItem) {
  return `/api/materials/${item.id}/file`
}

function isImageMaterial(item: MaterialItem) {
  return item.media_type.startsWith('image')
}

function isAudioMaterial(item: MaterialItem) {
  return item.media_type.startsWith('audio')
}

function materialTypeLabel(item: MaterialItem) {
  if (isAudioMaterial(item)) return '音频'
  if (isImageMaterial(item)) return '图片'
  return item.media_type || '素材'
}

async function refreshCategories() {
  categories.value = await getCategories()
  if (!activeCategory.value) {
    activeCategory.value =
      props.recommendedCategories?.find((name) => categories.value.some((item) => item.name === name)) ||
      categories.value[0]?.name ||
      ''
  }
}

async function refreshMaterials() {
  if (!activeCategory.value) {
    materials.value = []
    return
  }
  loading.value = true
  try {
    materials.value = (await getMaterials(activeCategory.value, 1, 80)).items
  } catch {
    toast('error', '加载素材失败')
  } finally {
    loading.value = false
  }
}

async function refreshSelections() {
  selections.value = await getSelectedMaterials(props.projectId)
}

async function refreshAll() {
  try {
    await scanMaterials()
  } catch {
    // Non-critical: backend may already have an index.
  }
  await Promise.all([refreshCategories(), refreshSelections()])
  await refreshMaterials()
}

async function toggle(item: MaterialItem) {
  try {
    if (selectedIds.value.has(item.id)) {
      await deselectMaterial(props.projectId, item.id)
    } else {
      await selectMaterial(props.projectId, item.id, item.category, selections.value.length)
    }
    await refreshSelections()
  } catch {
    toast('error', '更新素材选择失败')
  }
}

async function uploadFiles(files: File[]) {
  if (!files.length) return

  uploading.value = true
  uploadProgress.value = 0
  try {
    const payload = files.map((file) => ({
      file,
      relativePath: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
    }))
    const result = await uploadProjectMaterials(props.projectId, payload, true, (pct) => {
      uploadProgress.value = pct
    })
    toast('success', `已导入 ${result.files} 个素材`)
    await refreshAll()
  } catch {
    toast('error', '素材导入失败')
  } finally {
    uploading.value = false
  }
}

async function handleFiles(event: Event) {
  const input = event.target as HTMLInputElement
  try {
    await uploadFiles(Array.from(input.files || []))
  } finally {
    input.value = ''
  }
}

async function handleFolder(event: Event) {
  const input = event.target as HTMLInputElement
  try {
    await uploadFiles(Array.from(input.files || []))
  } finally {
    input.value = ''
  }
}

onMounted(refreshAll)
watch(activeCategory, refreshMaterials)
watch(() => props.projectId, refreshAll)
</script>

<template>
  <section class="grid h-full grid-cols-[260px_1fr] overflow-hidden">
    <aside class="border-r border-[#d8c9ad] bg-[#fff8ec] p-4">
      <div class="mb-4 flex items-center justify-between">
        <h2 class="font-semibold">素材库</h2>
        <button type="button" class="rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8]" @click="refreshAll">
          刷新
        </button>
      </div>

      <label class="mb-4 block rounded-lg border border-dashed border-[#cdbb97] bg-white/75 p-3 text-center text-xs text-[#6d5936] hover:bg-white">
        <input class="hidden" type="file" :accept="materialFileAccept" multiple :disabled="uploading" @change="handleFiles">
        {{ uploading ? `导入中 ${uploadProgress}%` : '上传图片 / 音频素材' }}
      </label>

      <label class="mb-4 block rounded-lg border border-dashed border-[#cdbb97] bg-white/75 p-3 text-center text-xs text-[#6d5936] hover:bg-white">
        <input class="hidden" type="file" multiple webkitdirectory directory :disabled="uploading" @change="handleFolder">
        {{ uploading ? `导入中 ${uploadProgress}%` : '导入素材文件夹' }}
      </label>

      <div class="space-y-2">
        <button
          v-for="category in categories"
          :key="category.name"
          type="button"
          class="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm"
          :class="activeCategory === category.name ? 'bg-[#7e9d53] text-white' : 'bg-white/70 text-[#6d5936] hover:bg-white'"
          @click="activeCategory = category.name"
        >
          <span class="truncate">{{ category.name }}</span>
          <span class="text-xs opacity-75">{{ category.count }}</span>
        </button>
      </div>
    </aside>

    <main class="overflow-auto p-6">
      <div class="mb-5 flex items-center justify-between">
        <div>
          <h2 class="text-xl font-semibold">选择素材</h2>
          <p class="text-sm text-[#867351]">已选 {{ selections.length }} 个素材，后续会用于提示词和视频生成。</p>
        </div>
      </div>

      <div v-if="recommendedCategories?.length" class="mb-4 flex flex-wrap gap-2">
        <button
          v-for="category in recommendedCategories"
          :key="category"
          type="button"
          class="rounded-full border border-[#d7c7a8] bg-[#fff8ec] px-3 py-1 text-xs text-[#6d5936]"
          @click="activeCategory = category"
        >
          推荐：{{ category }}
        </button>
      </div>

      <div v-if="loading" class="rounded-lg border border-[#d7c7a8] bg-white/75 p-5 text-sm text-[#867351]">加载素材中...</div>
      <div v-else-if="materials.length === 0" class="rounded-lg border border-[#d7c7a8] bg-white/75 p-5 text-sm text-[#867351]">当前分类没有素材。</div>
      <div v-else class="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
        <div
          v-for="item in materials"
          :key="item.id"
          class="overflow-hidden rounded-lg border bg-white text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow"
          :class="selectedIds.has(item.id) ? 'border-[#7e9d53] ring-2 ring-[#c9dda5]' : 'border-[#e2d5bf]'"
          role="button"
          tabindex="0"
          @click="toggle(item)"
          @keydown.enter.prevent="toggle(item)"
          @keydown.space.prevent="toggle(item)"
        >
          <img v-if="isImageMaterial(item)" :src="previewUrl(item)" :alt="item.filename" class="h-36 w-full object-cover">
          <div v-else-if="isAudioMaterial(item)" class="flex h-36 w-full items-center justify-center bg-[#f2e8d6] px-3">
            <audio class="w-full" controls :src="materialFileUrl(item)" @click.stop />
          </div>
          <video v-else :src="materialFileUrl(item)" class="h-36 w-full object-cover" muted />
          <div class="p-3">
            <div class="truncate text-sm font-medium">{{ item.filename }}</div>
            <div class="mt-1 flex items-center justify-between text-xs text-[#8a7857]">
              <span>{{ materialTypeLabel(item) }} · {{ item.category }}</span>
              <span>{{ selectedIds.has(item.id) ? '已选' : '选择' }}</span>
            </div>
          </div>
        </div>
      </div>
    </main>
  </section>
</template>
