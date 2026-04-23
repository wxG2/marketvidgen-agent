<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  createAdminApiKey,
  createApiKey,
  disableAdminApiKey,
  disableApiKey,
  listAdminApiKeys,
  listApiKeys,
} from '../../api/apiKeys'
import { toast } from '../../composables/useToast'
import type { AdminApiKeyRecord, ApiKeyRecord, ApiKeyScope, AuthUser } from '../../types'

const props = defineProps<{
  currentUser: AuthUser
  users: AuthUser[]
}>()

const DEFAULT_SCOPES: ApiKeyScope[] = ['video_jobs:create', 'video_jobs:read', 'video_jobs:review']

const scopeOptions: Array<{ value: ApiKeyScope; label: string; hint: string }> = [
  { value: 'video_jobs:create', label: '创建任务', hint: '允许调用 POST /v1/video-jobs' },
  { value: 'video_jobs:read', label: '查询状态', hint: '允许查询状态、SSE 进度和下载结果' },
  { value: 'video_jobs:review', label: '审核续跑', hint: '允许提交 shot_plan / replication_plan 审核' },
]

const adminView = ref<'mine' | 'customers'>('mine')
const myKeys = ref<ApiKeyRecord[]>([])
const customerKeys = ref<AdminApiKeyRecord[]>([])
const loadingMine = ref(false)
const loadingCustomers = ref(false)
const creatingMine = ref(false)
const creatingCustomer = ref(false)
const actingKeyId = ref<string | null>(null)
const selectedCustomerId = ref('')
const revealedKey = ref<{ ownerLabel: string; name: string; apiKey: string } | null>(null)

const myForm = reactive<{ name: string; scopes: ApiKeyScope[] }>({
  name: '',
  scopes: [...DEFAULT_SCOPES],
})

const customerForm = reactive<{ user_id: string; name: string; scopes: ApiKeyScope[] }>({
  user_id: '',
  name: '',
  scopes: [...DEFAULT_SCOPES],
})

const customerUsers = computed(() => props.users.filter((user) => user.id !== props.currentUser.id))
const selectedCustomer = computed(() => customerUsers.value.find((user) => user.id === selectedCustomerId.value) || null)
const filteredCustomerKeys = computed(() => customerKeys.value.filter((key) => key.user_id === selectedCustomerId.value))

watch(
  customerUsers,
  (users) => {
    if (users.length === 0) {
      selectedCustomerId.value = ''
      customerForm.user_id = ''
      return
    }
    if (!users.some((user) => user.id === selectedCustomerId.value)) {
      selectedCustomerId.value = users[0].id
    }
    customerForm.user_id = selectedCustomerId.value
  },
  { immediate: true },
)

watch(selectedCustomerId, (value) => {
  customerForm.user_id = value
})

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : '未使用'
}

function getErrorMessage(error: unknown, fallback: string) {
  const maybeError = error as { userMessage?: string; message?: string } | undefined
  return maybeError?.userMessage || maybeError?.message || fallback
}

function toggleScope(target: 'mine' | 'customer', scope: ApiKeyScope) {
  const scopes = target === 'mine' ? myForm.scopes : customerForm.scopes
  const next = scopes.includes(scope)
    ? scopes.filter((item) => item !== scope)
    : [...scopes, scope]

  if (target === 'mine') {
    myForm.scopes = next
  } else {
    customerForm.scopes = next
  }
}

function resetMineForm() {
  myForm.name = ''
  myForm.scopes = [...DEFAULT_SCOPES]
}

function resetCustomerForm() {
  customerForm.name = ''
  customerForm.scopes = [...DEFAULT_SCOPES]
  customerForm.user_id = selectedCustomerId.value
}

async function refreshMineKeys() {
  loadingMine.value = true
  try {
    myKeys.value = await listApiKeys()
  } catch (error) {
    toast('error', getErrorMessage(error, '加载 API Key 失败'))
  } finally {
    loadingMine.value = false
  }
}

async function refreshCustomerKeys() {
  if (props.currentUser.role !== 'admin') return
  loadingCustomers.value = true
  try {
    customerKeys.value = await listAdminApiKeys()
  } catch (error) {
    toast('error', getErrorMessage(error, '加载客户 API Key 失败'))
  } finally {
    loadingCustomers.value = false
  }
}

async function createOwnKey() {
  if (!myForm.name.trim()) {
    toast('warning', '请先填写 API Key 名称')
    return
  }

  creatingMine.value = true
  try {
    const created = await createApiKey({
      name: myForm.name.trim(),
      scopes: myForm.scopes,
    })
    revealedKey.value = {
      ownerLabel: props.currentUser.username,
      name: created.name,
      apiKey: created.api_key,
    }
    resetMineForm()
    await refreshMineKeys()
    toast('success', 'API Key 已创建，请立即复制并发给调用方')
  } catch (error) {
    toast('error', getErrorMessage(error, '创建 API Key 失败'))
  } finally {
    creatingMine.value = false
  }
}

async function createKeyForCustomer() {
  if (!selectedCustomerId.value) {
    toast('warning', '请先选择客户账号')
    return
  }
  if (!customerForm.name.trim()) {
    toast('warning', '请先填写 API Key 名称')
    return
  }

  creatingCustomer.value = true
  try {
    const created = await createAdminApiKey({
      user_id: selectedCustomerId.value,
      name: customerForm.name.trim(),
      scopes: customerForm.scopes,
    })
    revealedKey.value = {
      ownerLabel: selectedCustomer.value?.username || '目标客户',
      name: created.name,
      apiKey: created.api_key,
    }
    resetCustomerForm()
    await refreshCustomerKeys()
    toast('success', '客户 API Key 已创建，请现在复制并安全发放')
  } catch (error) {
    toast('error', getErrorMessage(error, '为客户创建 API Key 失败'))
  } finally {
    creatingCustomer.value = false
  }
}

async function disableOwnKey(record: ApiKeyRecord) {
  actingKeyId.value = record.id
  try {
    const updated = await disableApiKey(record.id)
    myKeys.value = myKeys.value.map((item) => (item.id === updated.id ? updated : item))
    toast('success', `已停用 ${record.name}`)
  } catch (error) {
    toast('error', getErrorMessage(error, '停用 API Key 失败'))
  } finally {
    actingKeyId.value = null
  }
}

async function disableCustomerKey(record: AdminApiKeyRecord) {
  actingKeyId.value = record.id
  try {
    const updated = await disableAdminApiKey(record.id)
    customerKeys.value = customerKeys.value.map((item) => (item.id === updated.id ? updated : item))
    toast('success', `已停用 ${record.name}`)
  } catch (error) {
    toast('error', getErrorMessage(error, '停用客户 API Key 失败'))
  } finally {
    actingKeyId.value = null
  }
}

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = value
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'absolute'
    textarea.style.left = '-9999px'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
}

async function copyRevealedKey() {
  if (!revealedKey.value) return
  await copyText(revealedKey.value.apiKey)
  toast('success', '完整 API Key 已复制')
}

onMounted(async () => {
  await refreshMineKeys()
  if (props.currentUser.role === 'admin') {
    await refreshCustomerKeys()
  }
})
</script>

<template>
  <section class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-[#d7c7a8] bg-white/85 p-5">
      <div>
        <h3 class="font-semibold">API Key 管理</h3>
        <p class="mt-1 text-sm text-[#867351]">
          给第三方调用 `/v1/video-jobs` 的 Bearer API Key 统一在这里管理。
        </p>
      </div>
      <div v-if="currentUser.role === 'admin'" class="rounded-lg bg-[#f2e8d6] p-1">
        <button
          type="button"
          class="rounded-md px-3 py-2 text-sm"
          :class="adminView === 'mine' ? 'bg-white shadow-sm' : ''"
          @click="adminView = 'mine'"
        >
          我的密钥
        </button>
        <button
          type="button"
          class="rounded-md px-3 py-2 text-sm"
          :class="adminView === 'customers' ? 'bg-white shadow-sm' : ''"
          @click="adminView = 'customers'"
        >
          客户密钥
        </button>
      </div>
    </div>

    <div class="rounded-lg border border-[#d7c7a8] bg-[#fff8ec] p-4 text-sm text-[#6d5936]">
      <div class="font-medium">使用说明</div>
      <div class="mt-2 space-y-1 text-[#867351]">
        <p>1. 创建后会返回一个完整的 `vg_...` Key，请立即复制并安全发给客户。</p>
        <p>2. 出于安全考虑，系统只保存哈希；离开当前提示后，后续列表只能看到前缀，不能再次查看完整 key。</p>
        <p>3. 如需删除，当前采用“停用”替代物理删除，避免连带清空历史外部任务记录。</p>
      </div>
    </div>

    <div v-if="revealedKey" class="rounded-lg border border-[#c7d8a3] bg-[#f4fae8] p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 class="font-semibold text-[#425628]">新 API Key 已创建</h4>
          <p class="mt-1 text-sm text-[#617646]">
            归属账号：{{ revealedKey.ownerLabel }} ｜ 名称：{{ revealedKey.name }}
          </p>
        </div>
        <div class="flex gap-2">
          <button
            type="button"
            class="rounded-lg border border-[#b9cd91] bg-white px-3 py-2 text-sm hover:bg-[#f8fff0]"
            @click="copyRevealedKey"
          >
            复制完整 Key
          </button>
          <button
            type="button"
            class="rounded-lg border border-[#b9cd91] px-3 py-2 text-sm hover:bg-[#eef7dc]"
            @click="revealedKey = null"
          >
            我已保存
          </button>
        </div>
      </div>
      <div class="mt-4 rounded-lg bg-[#31401f] p-4 font-mono text-sm text-[#f5f9ea] break-all">
        {{ revealedKey.apiKey }}
      </div>
    </div>

    <template v-if="adminView === 'mine' || currentUser.role !== 'admin'">
      <div class="grid gap-5 xl:grid-cols-[360px_1fr]">
        <section class="rounded-lg border border-[#d7c7a8] bg-white/85 p-5">
          <div class="mb-4">
            <h4 class="font-semibold">创建我的 API Key</h4>
            <p class="mt-1 text-sm text-[#867351]">创建后把完整 key 发给需要调用 VidGen 的合作方。</p>
          </div>

          <label class="mb-4 block">
            <span class="mb-1 block text-xs text-[#867351]">名称</span>
            <input
              v-model="myForm.name"
              class="w-full rounded-lg border border-[#d7c7a8] bg-white px-3 py-2 text-sm"
              placeholder="例如：customer-a-prod"
            >
          </label>

          <div class="space-y-2">
            <div class="text-xs text-[#867351]">权限</div>
            <label
              v-for="scope in scopeOptions"
              :key="scope.value"
              class="flex cursor-pointer items-start gap-3 rounded-lg border border-[#eadfca] bg-[#fff8ec] p-3"
            >
              <input
                :checked="myForm.scopes.includes(scope.value)"
                type="checkbox"
                class="mt-1"
                @change="toggleScope('mine', scope.value)"
              >
              <div>
                <div class="text-sm font-medium">{{ scope.label }}</div>
                <div class="mt-1 text-xs text-[#867351]">{{ scope.hint }}</div>
              </div>
            </label>
          </div>

          <button
            type="button"
            class="mt-4 w-full rounded-lg bg-[#7e9d53] px-4 py-2.5 text-sm text-white disabled:opacity-50"
            :disabled="creatingMine"
            @click="createOwnKey"
          >
            {{ creatingMine ? '创建中...' : '创建 API Key' }}
          </button>
        </section>

        <section class="rounded-lg border border-[#d7c7a8] bg-white/85">
          <div class="flex items-center justify-between border-b border-[#eadfca] px-4 py-3">
            <div>
              <h4 class="font-semibold">我的 API Keys</h4>
              <p class="mt-1 text-xs text-[#8a7857]">仅显示前缀、状态和使用记录；完整 key 只在创建时展示。</p>
            </div>
            <button
              type="button"
              class="rounded-lg border border-[#d7c7a8] px-3 py-2 text-xs hover:bg-[#f4ead8]"
              @click="refreshMineKeys"
            >
              刷新
            </button>
          </div>

          <div v-if="loadingMine" class="p-5 text-sm text-[#867351]">加载中...</div>
          <div v-else-if="myKeys.length === 0" class="p-5 text-sm text-[#867351]">当前账号还没有创建过 API Key。</div>
          <article
            v-for="record in myKeys"
            :key="record.id"
            class="border-b border-[#eadfca] p-4 last:border-0"
          >
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div class="flex flex-wrap items-center gap-2">
                  <div class="font-medium">{{ record.name }}</div>
                  <span
                    class="rounded-full px-2 py-0.5 text-xs"
                    :class="record.status === 'active' ? 'bg-[#e7f4d1] text-[#557230]' : 'bg-[#f3e8e2] text-[#8d5b42]'"
                  >
                    {{ record.status === 'active' ? '启用中' : '已停用' }}
                  </span>
                </div>
                <div class="mt-2 font-mono text-xs text-[#6d5936]">{{ record.key_prefix }}...</div>
                <div class="mt-2 flex flex-wrap gap-2">
                  <span
                    v-for="scope in record.scopes"
                    :key="scope"
                    class="rounded-full bg-[#f2e8d6] px-2 py-1 text-[11px] text-[#6d5936]"
                  >
                    {{ scope }}
                  </span>
                </div>
              </div>
              <button
                type="button"
                class="rounded-lg border border-[#d7c7a8] px-3 py-2 text-xs hover:bg-[#f4ead8] disabled:opacity-50"
                :disabled="record.status !== 'active' || actingKeyId === record.id"
                @click="disableOwnKey(record)"
              >
                {{ actingKeyId === record.id ? '停用中...' : record.status === 'active' ? '停用' : '已停用' }}
              </button>
            </div>
            <div class="mt-3 grid gap-2 text-xs text-[#8a7857] md:grid-cols-2">
              <div>创建时间：{{ formatDate(record.created_at) }}</div>
              <div>最后使用：{{ formatDate(record.last_used_at) }}</div>
            </div>
          </article>
        </section>
      </div>
    </template>

    <template v-else>
      <div v-if="customerUsers.length === 0" class="rounded-lg border border-[#d7c7a8] bg-white/85 p-5 text-sm text-[#867351]">
        还没有可管理的客户账号。先创建一个普通用户账号，再为其分配独立 API Key。
      </div>

      <div v-else class="grid gap-5 xl:grid-cols-[380px_1fr]">
        <section class="rounded-lg border border-[#d7c7a8] bg-white/85 p-5">
          <div class="mb-4">
            <h4 class="font-semibold">为客户创建 API Key</h4>
            <p class="mt-1 text-sm text-[#867351]">管理员可按客户账号隔离管理，单独发放生产和测试密钥。</p>
          </div>

          <label class="mb-4 block">
            <span class="mb-1 block text-xs text-[#867351]">客户账号</span>
            <select v-model="selectedCustomerId" class="w-full rounded-lg border border-[#d7c7a8] bg-white px-3 py-2 text-sm">
              <option v-for="user in customerUsers" :key="user.id" :value="user.id">
                {{ user.username }} · {{ user.is_active ? '启用' : '停用' }}
              </option>
            </select>
          </label>

          <label class="mb-4 block">
            <span class="mb-1 block text-xs text-[#867351]">名称</span>
            <input
              v-model="customerForm.name"
              class="w-full rounded-lg border border-[#d7c7a8] bg-white px-3 py-2 text-sm"
              placeholder="例如：customer-a-prod"
            >
          </label>

          <div class="space-y-2">
            <div class="text-xs text-[#867351]">权限</div>
            <label
              v-for="scope in scopeOptions"
              :key="scope.value"
              class="flex cursor-pointer items-start gap-3 rounded-lg border border-[#eadfca] bg-[#fff8ec] p-3"
            >
              <input
                :checked="customerForm.scopes.includes(scope.value)"
                type="checkbox"
                class="mt-1"
                @change="toggleScope('customer', scope.value)"
              >
              <div>
                <div class="text-sm font-medium">{{ scope.label }}</div>
                <div class="mt-1 text-xs text-[#867351]">{{ scope.hint }}</div>
              </div>
            </label>
          </div>

          <button
            type="button"
            class="mt-4 w-full rounded-lg bg-[#7e9d53] px-4 py-2.5 text-sm text-white disabled:opacity-50"
            :disabled="creatingCustomer || !selectedCustomer"
            @click="createKeyForCustomer"
          >
            {{ creatingCustomer ? '创建中...' : '为客户创建 API Key' }}
          </button>
        </section>

        <section class="rounded-lg border border-[#d7c7a8] bg-white/85">
          <div class="flex flex-wrap items-center justify-between gap-3 border-b border-[#eadfca] px-4 py-3">
            <div>
              <h4 class="font-semibold">客户 API Keys</h4>
              <p class="mt-1 text-xs text-[#8a7857]">
                当前查看：{{ selectedCustomer?.username || '未选择' }}
              </p>
            </div>
            <button
              type="button"
              class="rounded-lg border border-[#d7c7a8] px-3 py-2 text-xs hover:bg-[#f4ead8]"
              @click="refreshCustomerKeys"
            >
              刷新
            </button>
          </div>

          <div v-if="loadingCustomers" class="p-5 text-sm text-[#867351]">加载中...</div>
          <div v-else-if="filteredCustomerKeys.length === 0" class="p-5 text-sm text-[#867351]">
            当前客户还没有 API Key。
          </div>
          <article
            v-for="record in filteredCustomerKeys"
            :key="record.id"
            class="border-b border-[#eadfca] p-4 last:border-0"
          >
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div class="flex flex-wrap items-center gap-2">
                  <div class="font-medium">{{ record.name }}</div>
                  <span
                    class="rounded-full px-2 py-0.5 text-xs"
                    :class="record.status === 'active' ? 'bg-[#e7f4d1] text-[#557230]' : 'bg-[#f3e8e2] text-[#8d5b42]'"
                  >
                    {{ record.status === 'active' ? '启用中' : '已停用' }}
                  </span>
                </div>
                <div class="mt-2 font-mono text-xs text-[#6d5936]">{{ record.key_prefix }}...</div>
                <div class="mt-2 flex flex-wrap gap-2">
                  <span
                    v-for="scope in record.scopes"
                    :key="scope"
                    class="rounded-full bg-[#f2e8d6] px-2 py-1 text-[11px] text-[#6d5936]"
                  >
                    {{ scope }}
                  </span>
                </div>
              </div>
              <button
                type="button"
                class="rounded-lg border border-[#d7c7a8] px-3 py-2 text-xs hover:bg-[#f4ead8] disabled:opacity-50"
                :disabled="record.status !== 'active' || actingKeyId === record.id"
                @click="disableCustomerKey(record)"
              >
                {{ actingKeyId === record.id ? '停用中...' : record.status === 'active' ? '停用' : '已停用' }}
              </button>
            </div>
            <div class="mt-3 grid gap-2 text-xs text-[#8a7857] md:grid-cols-2">
              <div>创建时间：{{ formatDate(record.created_at) }}</div>
              <div>最后使用：{{ formatDate(record.last_used_at) }}</div>
            </div>
          </article>
        </section>
      </div>
    </template>
  </section>
</template>
