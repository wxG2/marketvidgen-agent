<script setup lang="ts">
defineProps<{
  size?: 'sm' | 'md' | 'lg'
  className?: string
}>()

const sizes = {
  sm: 'h-8 w-8',
  md: 'h-11 w-11',
  lg: 'h-20 w-20',
}
</script>

<template>
  <img
    src="/capy-avatar.svg"
    alt="capy"
    class="rounded-full border object-cover"
    :class="[sizes[size || 'md'], className]"
  >
</template>
