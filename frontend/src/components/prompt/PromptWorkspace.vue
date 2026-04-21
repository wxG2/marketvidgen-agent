<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import {
  generatePrompts,
  getChatHistory,
  getPrompts,
  getTemplates,
  sendChatMessage,
  updatePrompt,
} from '../../api/prompts'
import { getSelectedMaterials } from '../../api/materials'
import type { ChatMessage, MaterialSelection, Prompt, PromptTemplate } from '../../types'
import { toast } from '../../composables/useToast'

const props = defineProps<{
  projectId: string
}>()

const templates = ref<PromptTemplate[]>([])
const messages = ref<ChatMessage[]>([])
const prompts = ref<Prompt[]>([])
const selections = ref<MaterialSelection[]>([])
const input = ref('')
const streamText = ref('')
const streaming = ref(false)
const generating = ref(false)
const editingId = ref<string | null>(null)
const editText = ref('')
const messagesEnd = ref<HTMLElement | null>(null)

async function refresh() {
  const [templateList, chatList, promptList, selectionList] = await Promise.all([
    getTemplates().catch(() => []),
    getChatHistory(props.projectId).catch(() => []),
    getPrompts(props.projectId).catch(() => []),
    getSelectedMaterials(props.projectId).catch(() => []),
  ])
  templates.value = templateList
  messages.value = chatList
  prompts.value = promptList
  selections.value = selectionList
  await scrollToBottom()
}

async function scrollToBottom() {
  await nextTick()
  messagesEnd.value?.scrollIntoView({ block: 'end' })
}

async function send() {
  const content = input.value.trim()
  if (!content || streaming.value) return

  input.value = ''
  streamText.value = ''
  streaming.value = true
  messages.value.push({
    id: `local-${Date.now()}`,
    role: 'user',
    content,
    created_at: new Date().toISOString(),
  })

  try {
    await sendChatMessage(props.projectId, content, (chunk) => {
      streamText.value += chunk
      scrollToBottom()
    })
    await refresh()
  } catch {
    toast('error', '发送消息失败')
  } finally {
    streaming.value = false
    streamText.value = ''
  }
}

async function makePrompts() {
  generating.value = true
  try {
    prompts.value = await generatePrompts(props.projectId)
    toast('success', '提示词已生成')
  } catch {
    toast('error', '生成提示词失败')
  } finally {
    generating.value = false
  }
}

function startEdit(prompt: Prompt) {
  editingId.value = prompt.id
  editText.value = prompt.prompt_text
}

async function saveEdit(prompt: Prompt) {
  try {
    const updated = await updatePrompt(props.projectId, prompt.id, editText.value)
    prompts.value = prompts.value.map((item) => (item.id === updated.id ? updated : item))
    editingId.value = null
    toast('success', '提示词已保存')
  } catch {
    toast('error', '保存失败')
  }
}

onMounted(refresh)
watch(() => props.projectId, refresh)
</script>

<template>
  <section class="grid h-full grid-cols-[minmax(0,1fr)_380px] overflow-hidden">
    <main class="flex min-h-0 flex-col border-r border-[#d8c9ad]">
      <div class="border-b border-[#d8c9ad] bg-[#fff8ec] px-6 py-4">
        <h2 class="text-lg font-semibold">提示词工作区</h2>
        <p class="text-sm text-[#867351]">和 agent 对话，整理脚本和镜头提示词。</p>
      </div>

      <div class="min-h-0 flex-1 space-y-3 overflow-auto p-6">
        <article
          v-for="message in messages"
          :key="message.id"
          class="max-w-3xl rounded-lg border p-4 text-sm leading-6"
          :class="message.role === 'user' ? 'ml-auto border-[#c7d9a3] bg-[#f4f8e9]' : 'border-[#d7c7a8] bg-white/85'"
        >
          <div class="mb-1 text-xs font-medium text-[#867351]">{{ message.role === 'user' ? '你' : 'assistant' }}</div>
          <p class="whitespace-pre-wrap">{{ message.content }}</p>
        </article>

        <article v-if="streaming" class="max-w-3xl rounded-lg border border-[#d7c7a8] bg-white/85 p-4 text-sm leading-6">
          <div class="mb-1 text-xs font-medium text-[#867351]">assistant</div>
          <p class="whitespace-pre-wrap">{{ streamText || '正在思考...' }}</p>
        </article>
        <div ref="messagesEnd" />
      </div>

      <form class="border-t border-[#d8c9ad] bg-[#fff9ef] p-4" @submit.prevent="send">
        <div class="flex gap-2">
          <textarea
            v-model="input"
            class="min-h-20 flex-1 resize-none rounded-lg border border-[#daccb3] bg-white px-4 py-3 text-sm outline-none focus:border-[#8ca65c]"
            placeholder="描述你想要的视频风格、产品卖点或要调整的脚本..."
            @keydown.enter.exact.prevent="send"
          />
          <button
            type="submit"
            class="w-24 rounded-lg bg-[#7e9d53] text-sm text-white hover:bg-[#718f47] disabled:opacity-50"
            :disabled="streaming || !input.trim()"
          >
            发送
          </button>
        </div>
      </form>
    </main>

    <aside class="min-h-0 overflow-auto bg-[#fff8ec] p-5">
      <div class="mb-5 flex items-center justify-between">
        <h3 class="font-semibold">镜头提示词</h3>
        <button
          type="button"
          class="rounded-lg bg-[#7e9d53] px-3 py-2 text-xs text-white hover:bg-[#718f47] disabled:opacity-50"
          :disabled="generating"
          @click="makePrompts"
        >
          {{ generating ? '生成中...' : '生成提示词' }}
        </button>
      </div>

      <div v-if="selections.length" class="mb-5 rounded-lg border border-[#d7c7a8] bg-white/75 p-3">
        <div class="mb-2 text-xs font-medium text-[#867351]">已选素材</div>
        <div class="flex flex-wrap gap-2">
          <span v-for="item in selections" :key="item.id" class="rounded-full bg-[#edf5de] px-2 py-1 text-xs text-[#526b32]">
            {{ item.material?.filename || item.category }}
          </span>
        </div>
      </div>

      <div v-if="templates.length" class="mb-5 rounded-lg border border-[#d7c7a8] bg-white/75 p-3">
        <div class="mb-2 text-xs font-medium text-[#867351]">模板</div>
        <div class="space-y-2">
          <button
            v-for="template in templates"
            :key="template.name"
            type="button"
            class="w-full rounded-lg border border-[#eadfca] bg-[#fffaf1] px-3 py-2 text-left text-xs hover:bg-[#f4ead8]"
            @click="input = template.template"
          >
            <div class="font-medium">{{ template.name }}</div>
            <div class="mt-1 text-[#8a7857]">{{ template.description }}</div>
          </button>
        </div>
      </div>

      <div class="space-y-3">
        <article v-for="prompt in prompts" :key="prompt.id" class="rounded-lg border border-[#d7c7a8] bg-white p-3">
          <textarea
            v-if="editingId === prompt.id"
            v-model="editText"
            class="min-h-36 w-full resize-y rounded-lg border border-[#daccb3] p-3 text-sm outline-none focus:border-[#8ca65c]"
          />
          <p v-else class="whitespace-pre-wrap text-sm leading-6">{{ prompt.prompt_text }}</p>
          <div class="mt-3 flex justify-end gap-2">
            <button
              v-if="editingId === prompt.id"
              type="button"
              class="rounded-lg bg-[#7e9d53] px-3 py-1.5 text-xs text-white"
              @click="saveEdit(prompt)"
            >
              保存
            </button>
            <button
              type="button"
              class="rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8]"
              @click="editingId === prompt.id ? editingId = null : startEdit(prompt)"
            >
              {{ editingId === prompt.id ? '取消' : '编辑' }}
            </button>
          </div>
        </article>
      </div>
    </aside>
  </section>
</template>
