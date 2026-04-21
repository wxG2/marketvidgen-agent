<script setup lang="ts">
import WorkflowStepper from './WorkflowStepper.vue'
import CapyAvatar from '../ui/CapyAvatar.vue'

defineProps<{
  projectName: string
  currentUserName: string
  currentStep: number
  maxStep: number
  autoMode: boolean
}>()

const emit = defineEmits<{
  stepClick: [step: number]
  openDashboard: []
  openRepository: []
  logout: []
}>()
</script>

<template>
  <div class="flex h-screen flex-col bg-[#f8f0e1] text-[#4c3b22]">
    <header class="flex min-h-16 items-center justify-between gap-3 border-b border-[#d8c9ad] bg-[#fff9ef] px-4">
      <div class="flex min-w-0 items-center gap-3">
        <CapyAvatar size="sm" className="border-[#c9b687] bg-[#fff4dd]" />
        <div class="min-w-0">
          <div class="truncate text-sm font-semibold">{{ projectName }}</div>
          <div class="text-xs text-[#8a7857]">{{ currentUserName }}</div>
        </div>
      </div>

      <slot name="center">
        <WorkflowStepper
          v-if="!autoMode"
          :current-step="currentStep"
          :max-step="maxStep"
          @step-click="emit('stepClick', $event)"
        />
      </slot>

      <div class="flex shrink-0 items-center gap-2">
        <button
          type="button"
          class="rounded-lg border border-[#d7c7a8] bg-[#fff8ec] px-3 py-2 text-sm text-[#6d5936] hover:bg-[#f4ead8]"
          @click="emit('openRepository')"
        >
          仓库
        </button>
        <button
          type="button"
          class="rounded-lg border border-[#d7c7a8] bg-[#fff8ec] px-3 py-2 text-sm text-[#6d5936] hover:bg-[#f4ead8]"
          @click="emit('openDashboard')"
        >
          仪表盘
        </button>
        <button
          type="button"
          class="rounded-lg bg-[#4c3b22] px-3 py-2 text-sm text-white hover:bg-[#665033]"
          @click="emit('logout')"
        >
          退出
        </button>
      </div>
    </header>

    <main class="min-h-0 flex-1 overflow-hidden">
      <slot />
    </main>
  </div>
</template>
