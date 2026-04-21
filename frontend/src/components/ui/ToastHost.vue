<script setup lang="ts">
import { toasts, removeToast } from '../../composables/useToast'

const toneClass = {
  success: 'border-[#9eb66d] bg-[#f4f8e9] text-[#40512d]',
  error: 'border-[#d8927f] bg-[#fff1ec] text-[#7b2d1e]',
  warning: 'border-[#d6b46b] bg-[#fff8e5] text-[#70551b]',
  info: 'border-[#9bb4c9] bg-[#eef7fb] text-[#315065]',
}
</script>

<template>
  <div class="fixed right-4 top-4 z-50 space-y-2">
    <button
      v-for="item in toasts"
      :key="item.id"
      type="button"
      class="block w-80 rounded-lg border px-4 py-3 text-left text-sm shadow-sm transition hover:brightness-95"
      :class="toneClass[item.type]"
      @click="removeToast(item.id)"
    >
      {{ item.message }}
    </button>
  </div>
</template>
