<script setup lang="ts">
import { ref } from 'vue'
import { login, register } from '../../api/auth'
import type { AuthUser } from '../../types'
import CapyAvatar from '../ui/CapyAvatar.vue'
import { toast } from '../../composables/useToast'

const emit = defineEmits<{
  authenticated: [user: AuthUser]
}>()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const submitting = ref(false)

async function submit() {
  if (!username.value.trim() || !password.value.trim()) return
  submitting.value = true
  try {
    const payload = { username: username.value.trim(), password: password.value }
    const user = mode.value === 'login' ? await login(payload) : await register(payload)
    emit('authenticated', user)
    toast('success', mode.value === 'login' ? '登录成功' : '注册成功')
  } catch {
    toast('error', mode.value === 'login' ? '登录失败，请检查账号密码' : '注册失败，请稍后再试')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_top,_rgba(169,190,120,0.22),_transparent_30%),linear-gradient(180deg,#f8f0e1_0%,#eee2ca_100%)] px-4">
    <form class="w-full max-w-sm rounded-lg border border-[#d7c7a8] bg-white/86 p-6 shadow-sm backdrop-blur" @submit.prevent="submit">
      <div class="mb-6 text-center">
        <CapyAvatar size="lg" className="mx-auto mb-4 border-[#ccb98f] bg-[#faf1de]" />
        <h1 class="text-2xl font-bold text-[#4c3b22]">capy</h1>
        <p class="mt-1 text-sm text-[#7b6847]">登录后继续推进视频工作台</p>
      </div>

      <div class="mb-4 grid grid-cols-2 gap-2 rounded-lg bg-[#f2e8d6] p-1">
        <button
          type="button"
          class="rounded-md px-3 py-2 text-sm"
          :class="mode === 'login' ? 'bg-white text-[#4c3b22] shadow-sm' : 'text-[#7b6847]'"
          @click="mode = 'login'"
        >
          登录
        </button>
        <button
          type="button"
          class="rounded-md px-3 py-2 text-sm"
          :class="mode === 'register' ? 'bg-white text-[#4c3b22] shadow-sm' : 'text-[#7b6847]'"
          @click="mode = 'register'"
        >
          注册
        </button>
      </div>

      <label class="mb-3 block">
        <span class="mb-1 block text-xs text-[#7b6847]">用户名</span>
        <input
          v-model="username"
          class="w-full rounded-lg border border-[#daccb3] bg-[#fff8ec] px-4 py-2.5 text-sm outline-none focus:border-[#8ca65c]"
          autocomplete="username"
          placeholder="输入用户名"
        >
      </label>

      <label class="mb-5 block">
        <span class="mb-1 block text-xs text-[#7b6847]">密码</span>
        <input
          v-model="password"
          class="w-full rounded-lg border border-[#daccb3] bg-[#fff8ec] px-4 py-2.5 text-sm outline-none focus:border-[#8ca65c]"
          type="password"
          autocomplete="current-password"
          placeholder="输入密码"
        >
      </label>

      <button
        type="submit"
        class="w-full rounded-lg bg-[#7e9d53] px-4 py-2.5 text-sm font-medium text-white hover:bg-[#718f47] disabled:opacity-50"
        :disabled="submitting || !username.trim() || !password.trim()"
      >
        {{ submitting ? '处理中...' : (mode === 'login' ? '登录' : '创建账号') }}
      </button>
    </form>
  </div>
</template>
