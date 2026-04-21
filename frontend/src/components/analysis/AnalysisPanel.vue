<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { getAnalysis, triggerAnalysis } from '../../api/analysis'
import type { VideoAnalysis, VideoUpload } from '../../types'
import { toast } from '../../composables/useToast'

const props = defineProps<{
  projectId: string
  upload: VideoUpload | null
}>()

const emit = defineEmits<{
  complete: [analysis: VideoAnalysis]
}>()

const analysis = ref<VideoAnalysis | null>(null)
const loading = ref(false)

async function load() {
  try {
    analysis.value = await getAnalysis(props.projectId)
  } catch {
    analysis.value = null
  }
}

async function analyze() {
  loading.value = true
  try {
    analysis.value = await triggerAnalysis(props.projectId)
    emit('complete', analysis.value)
    toast('success', '分析任务已完成')
  } catch {
    toast('error', '分析失败，请确认已上传视频')
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.projectId, load)
</script>

<template>
  <section class="mx-auto max-w-4xl p-6">
    <div class="mb-5 flex items-center justify-between gap-4">
      <div>
        <h2 class="text-xl font-semibold">视频分析</h2>
        <p class="mt-1 text-sm text-[#867351]">提取场景摘要、标签和推荐素材分类。</p>
      </div>
      <button
        type="button"
        class="rounded-lg bg-[#7e9d53] px-4 py-2 text-sm text-white hover:bg-[#718f47] disabled:opacity-50"
        :disabled="loading || !upload"
        @click="analyze"
      >
        {{ loading ? '分析中...' : '开始分析' }}
      </button>
    </div>

    <div v-if="!upload" class="rounded-lg border border-[#d7c7a8] bg-[#fff8ec] p-5 text-sm text-[#7b6847]">
      请先上传参考视频。
    </div>

    <article v-else-if="analysis" class="rounded-lg border border-[#d7c7a8] bg-white/85 p-5">
      <div class="mb-4 flex items-center justify-between">
        <span class="font-medium">状态：{{ analysis.status }}</span>
        <span class="text-xs text-[#8a7857]">{{ analysis.completed_at || analysis.created_at }}</span>
      </div>
      <p class="whitespace-pre-wrap text-sm leading-6">{{ analysis.summary || '暂无摘要' }}</p>
      <div v-if="analysis.scene_tags?.length" class="mt-5">
        <div class="mb-2 text-xs font-medium text-[#867351]">场景标签</div>
        <div class="flex flex-wrap gap-2">
          <span v-for="tag in analysis.scene_tags" :key="tag" class="rounded-full bg-[#f2e8d6] px-3 py-1 text-xs">{{ tag }}</span>
        </div>
      </div>
      <div v-if="analysis.recommended_categories?.length" class="mt-5">
        <div class="mb-2 text-xs font-medium text-[#867351]">推荐素材分类</div>
        <div class="flex flex-wrap gap-2">
          <span v-for="category in analysis.recommended_categories" :key="category" class="rounded-full bg-[#edf5de] px-3 py-1 text-xs text-[#526b32]">
            {{ category }}
          </span>
        </div>
      </div>
      <p v-if="analysis.error_message" class="mt-4 rounded-lg bg-[#fff1ec] p-3 text-sm text-[#8a3a2b]">
        {{ analysis.error_message }}
      </p>
    </article>

    <div v-else class="rounded-lg border border-[#d7c7a8] bg-[#fff8ec] p-5 text-sm text-[#7b6847]">
      暂无分析记录。
    </div>
  </section>
</template>
