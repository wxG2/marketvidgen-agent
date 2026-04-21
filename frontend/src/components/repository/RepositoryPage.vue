<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { deleteUserUpload, listRepositoryAssets, listUserDeliveries, listUserUploads } from '../../api/repository'
import { deleteMaterial, getCategories, getMaterials } from '../../api/materials'
import type { MaterialCategory, MaterialItem, RepositoryAsset, RepositoryDelivery, RepositoryUpload } from '../../types'
import { toast } from '../../composables/useToast'

type Tab = 'deliveries' | 'uploads' | 'assets' | 'materials'

const props = defineProps<{
  pickerMode?: boolean
}>()

const emit = defineEmits<{
  back: []
  pickerConfirm: [items: MaterialItem[], upload: RepositoryUpload | null, delivery: RepositoryDelivery | null]
}>()

const tab = ref<Tab>(props.pickerMode ? 'materials' : 'deliveries')
const uploads = ref<RepositoryUpload[]>([])
const deliveries = ref<RepositoryDelivery[]>([])
const assets = ref<RepositoryAsset[]>([])
const categories = ref<MaterialCategory[]>([])
const activeCategory = ref('')
const materials = ref<MaterialItem[]>([])
const loading = ref(false)
const playingUploadId = ref<string | null>(null)
const playingDeliveryId = ref<string | null>(null)
const selectedItems = ref<Map<string, MaterialItem>>(new Map())
const selectedUploadId = ref<string | null>(null)
const selectedDeliveryId = ref<string | null>(null)

const selectedUpload = computed(() => uploads.value.find((item) => item.id === selectedUploadId.value) || null)
const selectedDelivery = computed(() => deliveries.value.find((item) => item.id === selectedDeliveryId.value) || null)

function formatBytes(bytes: number) {
  if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes > 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${bytes} B`
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString()
}

function artifactTypeLabel(asset: RepositoryAsset) {
  const labels: Record<string, string> = {
    plan: '方案',
    prompt: '提示词',
    voice_params: '配音',
    status: '状态',
    audio: '音频',
    subtitle: '字幕',
    video_manifest: '视频清单',
    video: '视频',
  }
  return labels[asset.asset_type] || asset.asset_type
}

function isVideoArtifact(asset: RepositoryAsset) {
  return asset.asset_type === 'video' || asset.mime_type?.startsWith('video/')
}

function isAudioArtifact(asset: RepositoryAsset) {
  return asset.asset_type === 'audio' || asset.mime_type?.startsWith('audio/')
}

function compactText(text: string | null) {
  if (!text) return ''
  return text.length > 700 ? `${text.slice(0, 700)}...` : text
}

function materialFileUrl(item: MaterialItem) {
  return `/api/materials/${item.id}/file`
}

async function refreshUploads() {
  uploads.value = await listUserUploads().catch(() => [])
}

async function refreshDeliveries() {
  deliveries.value = await listUserDeliveries().catch(() => [])
}

async function refreshAssets() {
  assets.value = await listRepositoryAssets().catch(() => [])
}

async function refreshCategories() {
  categories.value = await getCategories().catch(() => [])
  activeCategory.value = activeCategory.value || categories.value[0]?.name || ''
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

async function refreshAll() {
  await Promise.all([refreshUploads(), refreshDeliveries(), refreshAssets(), refreshCategories()])
  await refreshMaterials()
}

function toggleMaterial(item: MaterialItem) {
  const next = new Map(selectedItems.value)
  if (next.has(item.id)) next.delete(item.id)
  else next.set(item.id, item)
  selectedItems.value = next
}

async function removeUpload(upload: RepositoryUpload) {
  try {
    await deleteUserUpload(upload.id)
    uploads.value = uploads.value.filter((item) => item.id !== upload.id)
    toast('success', '上传记录已删除')
  } catch {
    toast('error', '删除上传记录失败')
  }
}

async function removeMaterial(item: MaterialItem) {
  try {
    await deleteMaterial(item.id)
    materials.value = materials.value.filter((value) => value.id !== item.id)
    toast('success', '素材已删除')
  } catch {
    toast('error', '删除素材失败')
  }
}

function confirmPicker() {
  emit('pickerConfirm', Array.from(selectedItems.value.values()), selectedUpload.value, selectedDelivery.value)
}

onMounted(refreshAll)
watch(activeCategory, refreshMaterials)
</script>

<template>
  <section class="flex h-full flex-col overflow-hidden bg-[#f8f0e1]">
    <header class="flex items-center justify-between border-b border-[#d8c9ad] bg-[#fff8ec] px-6 py-4">
      <div class="flex items-center gap-3">
        <button type="button" class="rounded-lg border border-[#d7c7a8] px-3 py-2 text-sm hover:bg-[#f4ead8]" @click="emit('back')">
          返回
        </button>
        <div>
          <h2 class="text-lg font-semibold">个人仓库</h2>
          <p class="text-sm text-[#867351]">查看上传、素材和已保存成片。</p>
        </div>
      </div>
      <button
        v-if="pickerMode"
        type="button"
        class="rounded-lg bg-[#7e9d53] px-4 py-2 text-sm text-white hover:bg-[#718f47]"
        @click="confirmPicker"
      >
        确认选择
      </button>
    </header>

    <div class="flex min-h-0 flex-1">
      <aside class="w-56 border-r border-[#d8c9ad] bg-[#fff8ec] p-4">
        <button
          v-for="item in [
            { key: 'deliveries', label: '成片' },
            { key: 'uploads', label: '参考视频' },
            { key: 'assets', label: 'Agent 产物' },
            { key: 'materials', label: '素材' },
          ]"
          :key="item.key"
          type="button"
          class="mb-2 w-full rounded-lg px-3 py-2 text-left text-sm"
          :class="tab === item.key ? 'bg-[#7e9d53] text-white' : 'bg-white/75 text-[#6d5936] hover:bg-white'"
          @click="tab = item.key as Tab"
        >
          {{ item.label }}
        </button>
      </aside>

      <main class="min-w-0 flex-1 overflow-auto p-6">
        <div v-if="tab === 'deliveries'" class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <article v-for="delivery in deliveries" :key="delivery.id" class="rounded-lg border border-[#d7c7a8] bg-white/85 p-4">
            <div class="mb-3 flex items-start justify-between gap-3">
              <div>
                <div class="font-medium">{{ delivery.title || '未命名成片' }}</div>
                <div class="mt-1 text-xs text-[#8a7857]">{{ delivery.project_name }} · {{ formatDate(delivery.created_at) }}</div>
              </div>
              <input v-if="pickerMode" v-model="selectedDeliveryId" type="radio" :value="delivery.id">
            </div>
            <video v-if="delivery.video_url" class="h-52 w-full rounded-lg bg-black object-contain" controls :src="delivery.video_url" />
            <div v-else class="flex h-52 items-center justify-center rounded-lg bg-[#f2e8d6] text-sm text-[#867351]">{{ delivery.status }}</div>
            <p v-if="delivery.description" class="mt-3 line-clamp-3 text-sm text-[#6d5936]">{{ delivery.description }}</p>
          </article>
        </div>

        <div v-else-if="tab === 'uploads'" class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <article v-for="upload in uploads" :key="upload.id" class="rounded-lg border border-[#d7c7a8] bg-white/85 p-4">
            <div class="mb-3 flex items-start justify-between gap-3">
              <div>
                <div class="font-medium">{{ upload.filename }}</div>
                <div class="mt-1 text-xs text-[#8a7857]">{{ upload.project_name }} · {{ formatBytes(upload.file_size) }}</div>
              </div>
              <input v-if="pickerMode" v-model="selectedUploadId" type="radio" :value="upload.id">
            </div>
            <video v-if="playingUploadId === upload.id" class="h-52 w-full rounded-lg bg-black object-contain" controls :src="upload.stream_url" />
            <button v-else type="button" class="flex h-52 w-full items-center justify-center rounded-lg bg-[#f2e8d6] text-sm text-[#6d5936]" @click="playingUploadId = upload.id">
              播放预览
            </button>
            <div class="mt-3 flex items-center justify-between">
              <span class="text-xs text-[#8a7857]">{{ formatDate(upload.created_at) }}</span>
              <button v-if="!pickerMode" type="button" class="rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8]" @click="removeUpload(upload)">
                删除
              </button>
            </div>
          </article>
        </div>

        <div v-else-if="tab === 'assets'" class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <article v-for="asset in assets" :key="asset.id" class="rounded-lg border border-[#d7c7a8] bg-white/85 p-4">
            <div class="mb-3">
              <div class="font-medium">{{ asset.title || asset.asset_key }}</div>
              <div class="mt-1 text-xs text-[#8a7857]">
                {{ asset.project_name || '项目' }} · {{ asset.source_agent }} · {{ artifactTypeLabel(asset) }}
              </div>
            </div>
            <video v-if="isVideoArtifact(asset) && asset.file_url" class="h-52 w-full rounded-lg bg-black object-contain" controls :src="asset.file_url" />
            <audio v-else-if="isAudioArtifact(asset) && asset.file_url" class="mt-2 w-full" controls :src="asset.file_url" />
            <pre v-else-if="asset.text_content" class="max-h-52 overflow-auto whitespace-pre-wrap rounded-lg bg-[#fff8ec] p-3 text-xs leading-5 text-[#6d5936]">{{ compactText(asset.text_content) }}</pre>
            <div v-else class="flex h-52 items-center justify-center rounded-lg bg-[#f2e8d6] text-sm text-[#867351]">暂无预览</div>
            <div class="mt-3 flex items-center justify-between gap-2">
              <span class="truncate text-xs text-[#8a7857]">{{ formatDate(asset.created_at) }}</span>
              <a v-if="asset.file_url" class="rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8]" :href="asset.file_url" target="_blank" rel="noreferrer">
                打开文件
              </a>
            </div>
          </article>
        </div>

        <div v-else>
          <div class="mb-5 flex flex-wrap gap-2">
            <button
              v-for="category in categories"
              :key="category.name"
              type="button"
              class="rounded-lg px-3 py-2 text-sm"
              :class="activeCategory === category.name ? 'bg-[#7e9d53] text-white' : 'border border-[#d7c7a8] bg-[#fff8ec] text-[#6d5936]'"
              @click="activeCategory = category.name"
            >
              {{ category.name }} · {{ category.count }}
            </button>
          </div>

          <div v-if="loading" class="rounded-lg border border-[#d7c7a8] bg-white/75 p-5 text-sm text-[#867351]">加载中...</div>
          <div v-else class="grid gap-4 md:grid-cols-3 xl:grid-cols-4">
            <article
              v-for="item in materials"
              :key="item.id"
              class="overflow-hidden rounded-lg border bg-white/90"
              :class="selectedItems.has(item.id) ? 'border-[#7e9d53] ring-2 ring-[#c9dda5]' : 'border-[#d7c7a8]'"
            >
              <button type="button" class="block w-full text-left" @click="pickerMode ? toggleMaterial(item) : undefined">
                <img v-if="item.media_type.startsWith('image')" class="h-36 w-full object-cover" :src="item.thumbnail_url || materialFileUrl(item)" :alt="item.filename">
                <video v-else class="h-36 w-full object-cover" :src="materialFileUrl(item)" muted />
                <div class="p-3">
                  <div class="truncate text-sm font-medium">{{ item.filename }}</div>
                  <div class="mt-1 text-xs text-[#8a7857]">{{ item.category }}</div>
                </div>
              </button>
              <div class="border-t border-[#eadfca] p-3">
                <button v-if="!pickerMode" type="button" class="rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8]" @click="removeMaterial(item)">
                  删除
                </button>
                <span v-else class="text-xs text-[#8a7857]">{{ selectedItems.has(item.id) ? '已选' : '点击选择' }}</span>
              </div>
            </article>
          </div>
        </div>
      </main>
    </div>
  </section>
</template>
