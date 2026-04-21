<script setup lang="ts">
const props = defineProps<{
  currentStep: number
  maxStep: number
}>()

const emit = defineEmits<{
  stepClick: [step: number]
}>()

const steps = [
  '上传',
  '分析',
  '素材',
  '提示词',
  '生成',
  '复核',
  '剪辑',
]

function canOpen(step: number) {
  return step <= Math.max(props.maxStep, props.currentStep)
}
</script>

<template>
  <nav class="flex items-center gap-1 overflow-x-auto px-2">
    <button
      v-for="(label, index) in steps"
      :key="label"
      type="button"
      class="min-w-20 rounded-lg border px-3 py-2 text-xs transition"
      :class="[
        currentStep === index + 1
          ? 'border-[#7e9d53] bg-[#7e9d53] text-white'
          : 'border-[#d8c9ad] bg-[#fff8ec] text-[#6d5936]',
        canOpen(index + 1) ? 'hover:bg-[#f3ead9]' : 'cursor-not-allowed opacity-45',
      ]"
      :disabled="!canOpen(index + 1)"
      @click="emit('stepClick', index + 1)"
    >
      <span class="mr-1 font-semibold">{{ index + 1 }}</span>{{ label }}
    </button>
  </nav>
</template>
