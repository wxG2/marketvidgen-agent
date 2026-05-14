<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { RemixPlan, RemixSegmentEdit } from '../../types'

const props = defineProps<{
  plan: RemixPlan | null
  loading?: boolean
}>()

const emit = defineEmits<{
  confirm: [payload: { approved: boolean; adjustments?: string | null; edited_segments?: RemixSegmentEdit[] }]
}>()

const adjustments = ref('')
const edits = ref<Record<number, RemixSegmentEdit>>({})

watch(
  () => props.plan,
  () => {
    edits.value = {}
    adjustments.value = ''
  },
)

const visibleSegments = computed(() => props.plan?.segments || [])
const totalDuration = computed(() => {
  return visibleSegments.value
    .filter((segment) => !edits.value[segment.segment_idx]?.removed)
    .reduce((sum, segment) => {
      const edit = edits.value[segment.segment_idx]
      const start = edit?.start_seconds ?? segment.start_seconds
      const end = edit?.end_seconds ?? segment.end_seconds
      return sum + Math.max(end - start, 0)
    }, 0)
})

function patchSegment(segmentIdx: number, patch: Partial<RemixSegmentEdit>) {
  edits.value[segmentIdx] = {
    segment_idx: segmentIdx,
    ...(edits.value[segmentIdx] || {}),
    ...patch,
  }
}

function segmentVoiceover(segment: RemixPlan['segments'][number]) {
  return segment.voiceover || segment.narration || segment.script_segment || ''
}

function submitApproved() {
  emit('confirm', {
    approved: true,
    edited_segments: Object.values(edits.value),
  })
}

function submitAdjustment() {
  emit('confirm', {
    approved: false,
    adjustments: adjustments.value.trim() || null,
  })
}
</script>

<template>
  <div class="mb-4 rounded-lg border border-[#9bbf98] bg-[#f3faef] p-4">
    <div class="mb-3 flex flex-wrap items-start justify-between gap-2">
      <div>
        <div class="text-sm font-semibold text-[#3f6638]">{{ plan?.title || '混剪方案待确认' }}</div>
        <p v-if="plan?.concept" class="mt-1 text-xs leading-5 text-[#607455]">{{ plan.concept }}</p>
      </div>
      <div class="rounded-md bg-white/70 px-2 py-1 text-xs text-[#526b32]">
        {{ totalDuration.toFixed(1) }}s
      </div>
    </div>

    <div v-if="plan?.analysis_report" class="mb-3 rounded-md bg-white/70 p-2 text-xs leading-5 text-[#5f6f56]">
      {{ plan.analysis_report }}
    </div>

    <div class="max-h-80 space-y-2 overflow-auto">
      <div
        v-for="segment in visibleSegments"
        :key="segment.segment_idx"
        class="rounded-lg bg-white/80 p-2.5 text-xs"
        :class="{ 'opacity-50': edits[segment.segment_idx]?.removed }"
      >
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div class="font-medium text-[#3f6638]">
            片段 {{ segment.segment_idx + 1 }}
            <span class="ml-1 font-normal text-[#7a8b70]">{{ segment.role || 'segment' }}</span>
          </div>
          <label class="inline-flex items-center gap-1 text-[#7a4d3a]">
            <input
              type="checkbox"
              :checked="Boolean(edits[segment.segment_idx]?.removed)"
              @change="(event) => patchSegment(segment.segment_idx, { removed: (event.target as HTMLInputElement).checked })"
            >
            移除
          </label>
        </div>

        <p v-if="segment.description" class="mb-2 leading-5 text-[#5f6f56]">{{ segment.description }}</p>
        <p v-if="segmentVoiceover(segment)" class="mb-2 rounded-md bg-[#f6f2e9] px-2 py-1.5 leading-5 text-[#725f2e]">
          旁白：{{ segmentVoiceover(segment) }}
        </p>

        <div class="grid grid-cols-2 gap-2">
          <label class="block text-[#66745d]">
            开始
            <input
              type="number"
              min="0"
              step="0.1"
              class="mt-1 w-full rounded border border-[#cbd8bd] bg-white px-2 py-1 outline-none focus:border-[#7e9d53]"
              :value="edits[segment.segment_idx]?.start_seconds ?? segment.start_seconds"
              @input="(event) => patchSegment(segment.segment_idx, { start_seconds: Number((event.target as HTMLInputElement).value) })"
            >
          </label>
          <label class="block text-[#66745d]">
            结束
            <input
              type="number"
              min="0"
              step="0.1"
              class="mt-1 w-full rounded border border-[#cbd8bd] bg-white px-2 py-1 outline-none focus:border-[#7e9d53]"
              :value="edits[segment.segment_idx]?.end_seconds ?? segment.end_seconds"
              @input="(event) => patchSegment(segment.segment_idx, { end_seconds: Number((event.target as HTMLInputElement).value) })"
            >
          </label>
        </div>

        <div class="mt-2 flex flex-wrap items-center justify-between gap-2 text-[#7a8b70]">
          <span>源视频 {{ segment.source_video_id }}</span>
          <span v-if="segment.quality_score">质量 {{ Number(segment.quality_score).toFixed(1) }}</span>
        </div>
      </div>
    </div>

    <textarea
      v-model="adjustments"
      class="mt-3 min-h-20 w-full rounded-lg border border-[#cbd8bd] bg-white p-2 text-sm outline-none focus:border-[#7e9d53]"
      placeholder="需要调整的地方，可留空直接确认。"
    />

    <div class="mt-3 flex flex-wrap gap-2">
      <button
        type="button"
        class="rounded-lg bg-[#6f944f] px-3 py-2 text-xs text-white disabled:opacity-50"
        :disabled="loading"
        @click="submitApproved"
      >
        确认混剪
      </button>
      <button
        type="button"
        class="rounded-lg border border-[#b8caa9] px-3 py-2 text-xs text-[#526b32] hover:bg-white disabled:opacity-50"
        :disabled="loading"
        @click="submitAdjustment"
      >
        提交调整
      </button>
    </div>
  </div>
</template>
