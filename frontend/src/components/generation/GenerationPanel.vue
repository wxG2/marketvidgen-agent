<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { getPromptBindings } from '../../api/prompts'
import {
  deselectVideo,
  generateSingle,
  getGeneratedVideoUrl,
  getGenerations,
  selectVideo,
  startGeneration,
} from '../../api/generation'
import type { GeneratedVideo, PromptBinding } from '../../types'
import { toast } from '../../composables/useToast'

const props = defineProps<{
  projectId: string
}>()

const bindings = ref<PromptBinding[]>([])
const videos = ref<GeneratedVideo[]>([])
const generatingAll = ref(false)
const generatingIds = ref<Set<string>>(new Set())
let timer: number | undefined

const hasProcessing = computed(() => videos.value.some((video) => video.status === 'pending' || video.status === 'processing'))

async function refresh() {
  const [bindingList, videoList] = await Promise.all([
    getPromptBindings(props.projectId).catch(() => []),
    getGenerations(props.projectId).catch(() => []),
  ])
  bindings.value = bindingList
  videos.value = videoList
}

function videoForPrompt(promptId: string) {
  return videos.value.find((video) => video.prompt_id === promptId)
}

async function generateAll() {
  generatingAll.value = true
  try {
    videos.value = await startGeneration(props.projectId)
    toast('success', '已提交生成任务')
    schedulePolling()
  } catch {
    toast('error', '启动生成失败')
  } finally {
    generatingAll.value = false
  }
}

async function generateOne(promptId: string) {
  generatingIds.value = new Set(generatingIds.value).add(promptId)
  try {
    const video = await generateSingle(props.projectId, promptId)
    videos.value = [video, ...videos.value.filter((item) => item.id !== video.id)]
    schedulePolling()
  } catch {
    toast('error', '生成当前镜头失败')
  } finally {
    const next = new Set(generatingIds.value)
    next.delete(promptId)
    generatingIds.value = next
  }
}

async function toggleSelected(video: GeneratedVideo) {
  try {
    if (video.is_selected) await deselectVideo(props.projectId, video.id)
    else await selectVideo(props.projectId, video.id)
    await refresh()
  } catch {
    toast('error', '更新选择失败')
  }
}

function schedulePolling() {
  window.clearInterval(timer)
  timer = window.setInterval(async () => {
    await refresh()
    if (!hasProcessing.value) window.clearInterval(timer)
  }, 3500)
}

onMounted(async () => {
  await refresh()
  if (hasProcessing.value) schedulePolling()
})
watch(() => props.projectId, refresh)
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <section class="mx-auto max-w-6xl p-6">
    <div class="mb-5 flex items-center justify-between gap-4">
      <div>
        <h2 class="text-xl font-semibold">视频生成</h2>
        <p class="mt-1 text-sm text-[#867351]">按镜头提示词生成视频片段，并选择进入剪辑的版本。</p>
      </div>
      <button
        type="button"
        class="rounded-lg bg-[#7e9d53] px-4 py-2 text-sm text-white hover:bg-[#718f47] disabled:opacity-50"
        :disabled="generatingAll || bindings.length === 0"
        @click="generateAll"
      >
        {{ generatingAll ? '提交中...' : '全部生成' }}
      </button>
    </div>

    <div v-if="bindings.length === 0" class="rounded-lg border border-[#d7c7a8] bg-[#fff8ec] p-5 text-sm text-[#867351]">
      暂无提示词绑定，请先生成提示词。
    </div>

    <div class="grid gap-4 md:grid-cols-2">
      <article
        v-for="binding in bindings"
        :key="binding.prompt_id"
        class="rounded-lg border border-[#d7c7a8] bg-white/85 p-4 shadow-sm"
      >
        <div class="mb-3 flex items-start justify-between gap-3">
          <div>
            <div class="text-sm font-semibold">{{ binding.material_filename || '无绑定素材' }}</div>
            <p class="mt-1 line-clamp-3 text-xs leading-5 text-[#6d5936]">{{ binding.prompt_text }}</p>
          </div>
          <button
            type="button"
            class="rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8] disabled:opacity-50"
            :disabled="generatingIds.has(binding.prompt_id)"
            @click="generateOne(binding.prompt_id)"
          >
            {{ generatingIds.has(binding.prompt_id) ? '生成中' : '重生成' }}
          </button>
        </div>

        <template v-if="videoForPrompt(binding.prompt_id)">
          <video
            v-if="videoForPrompt(binding.prompt_id)?.video_url || videoForPrompt(binding.prompt_id)?.status === 'completed'"
            class="h-64 w-full rounded-lg bg-black object-contain"
            controls
            :poster="videoForPrompt(binding.prompt_id)?.thumbnail_url || undefined"
            :src="getGeneratedVideoUrl(videoForPrompt(binding.prompt_id)!.id)"
          />
          <div v-else class="flex h-64 items-center justify-center rounded-lg bg-[#f2e8d6] text-sm text-[#867351]">
            {{ videoForPrompt(binding.prompt_id)?.status }}
          </div>
          <div class="mt-3 flex items-center justify-between">
            <span class="rounded-full bg-[#f2e8d6] px-3 py-1 text-xs">{{ videoForPrompt(binding.prompt_id)?.status }}</span>
            <button
              type="button"
              class="rounded-lg px-3 py-1.5 text-xs"
              :class="videoForPrompt(binding.prompt_id)?.is_selected ? 'bg-[#7e9d53] text-white' : 'border border-[#d7c7a8] hover:bg-[#f4ead8]'"
              @click="toggleSelected(videoForPrompt(binding.prompt_id)!)"
            >
              {{ videoForPrompt(binding.prompt_id)?.is_selected ? '已选' : '选入剪辑' }}
            </button>
          </div>
          <p v-if="videoForPrompt(binding.prompt_id)?.error_message" class="mt-3 rounded-lg bg-[#fff1ec] p-3 text-xs text-[#8a3a2b]">
            {{ videoForPrompt(binding.prompt_id)?.error_message }}
          </p>
        </template>
        <div v-else class="flex h-64 items-center justify-center rounded-lg border border-dashed border-[#d7c7a8] text-sm text-[#867351]">
          尚未生成
        </div>
      </article>
    </div>
  </section>
</template>
