<script setup lang="ts">
import { ref } from 'vue'
import { getVideoStreamUrl, uploadVideo } from '../../api/upload'
import type { VideoUpload } from '../../types'
import { toast } from '../../composables/useToast'

const props = defineProps<{
  projectId: string
  upload: VideoUpload | null
}>()

const emit = defineEmits<{
  uploaded: [upload: VideoUpload]
}>()

const uploading = ref(false)
const progress = ref(0)

async function handleFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  uploading.value = true
  progress.value = 0
  try {
    const result = await uploadVideo(props.projectId, file, (pct) => {
      progress.value = pct
    })
    emit('uploaded', result)
    toast('success', '视频上传完成')
  } catch {
    toast('error', '上传失败，请检查后端服务或文件格式')
  } finally {
    uploading.value = false
    input.value = ''
  }
}
</script>

<template>
  <section class="mx-auto max-w-4xl p-6">
    <div class="mb-5">
      <h2 class="text-xl font-semibold">上传参考视频</h2>
      <p class="mt-1 text-sm text-[#867351]">上传后可进入分析、素材选择和生成流程。</p>
    </div>

    <label class="flex min-h-56 cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-[#cdbb97] bg-[#fff8ec] p-8 text-center hover:bg-[#f7ecd7]">
      <input class="hidden" type="file" accept="video/*" :disabled="uploading" @change="handleFile">
      <span class="text-lg font-medium text-[#4c3b22]">{{ uploading ? `上传中 ${progress}%` : '选择视频文件' }}</span>
      <span class="mt-2 text-sm text-[#867351]">支持后端允许的常见视频格式。</span>
      <div v-if="uploading" class="mt-4 h-2 w-full max-w-sm overflow-hidden rounded-full bg-[#eadfca]">
        <div class="h-full bg-[#7e9d53]" :style="{ width: `${progress}%` }" />
      </div>
    </label>

    <article v-if="upload" class="mt-6 rounded-lg border border-[#d7c7a8] bg-white/85 p-4">
      <div class="mb-3 flex items-center justify-between gap-3">
        <div>
          <div class="font-medium">{{ upload.filename }}</div>
          <div class="text-xs text-[#8a7857]">
            {{ upload.duration_seconds ? `${Math.round(upload.duration_seconds)} 秒` : '时长待分析' }}
          </div>
        </div>
        <span class="rounded-full bg-[#edf5de] px-3 py-1 text-xs text-[#526b32]">已上传</span>
      </div>
      <video class="max-h-[420px] w-full rounded-lg bg-black" controls :src="getVideoStreamUrl(upload.id)" />
    </article>
  </section>
</template>
