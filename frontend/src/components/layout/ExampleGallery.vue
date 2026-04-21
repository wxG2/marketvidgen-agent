<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listExamples } from '../../api/examples'
import type { ExampleCategory, ExampleFile } from '../../types'

const categories = ref<ExampleCategory[]>([])
const loading = ref(true)

function formatSize(size: number) {
  if (size > 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  if (size > 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

function isImage(file: ExampleFile) {
  return file.asset_type === 'image'
}

function isVideo(file: ExampleFile) {
  return file.asset_type === 'video'
}

onMounted(async () => {
  try {
    categories.value = (await listExamples()).categories
  } catch {
    categories.value = []
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="mt-6 rounded-lg border border-[#d7c7a8] bg-white/80 p-5 shadow-sm backdrop-blur">
    <div class="mb-4 flex items-center justify-between">
      <h2 class="text-sm font-semibold text-[#4c3b22]">示例素材</h2>
      <span class="text-xs text-[#8a7857]">{{ loading ? '加载中' : `${categories.length} 组` }}</span>
    </div>

    <div v-if="!loading && categories.length === 0" class="text-sm text-[#8a7857]">
      暂无示例素材。
    </div>

    <div class="grid gap-3">
      <article
        v-for="category in categories"
        :key="category.name"
        class="rounded-lg border border-[#eadfca] bg-[#fff8ed] p-3"
      >
        <div class="mb-3 flex items-center justify-between">
          <div class="text-sm font-medium">{{ category.name }}</div>
          <div class="text-xs text-[#8a7857]">{{ category.files.length }} 个文件</div>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <a
            v-for="file in category.files.slice(0, 4)"
            :key="file.relative_path"
            :href="file.url"
            target="_blank"
            class="min-h-24 overflow-hidden rounded-lg border border-[#e6dbc8] bg-white text-xs text-[#6d5936] hover:border-[#9eb66d]"
          >
            <img v-if="isImage(file)" :src="file.url" :alt="file.name" class="h-20 w-full object-cover">
            <video v-else-if="isVideo(file)" :src="file.url" class="h-20 w-full object-cover" muted />
            <div v-else class="flex h-20 items-center justify-center bg-[#f2e8d6]">文件</div>
            <div class="truncate px-2 py-1">{{ file.name }} · {{ formatSize(file.size) }}</div>
          </a>
        </div>
      </article>
    </div>
  </section>
</template>
