import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  createAdminApiKey,
  createApiKey,
  disableAdminApiKey,
  disableApiKey,
  listAdminApiKeys,
  listApiKeys,
} from '../api/apiKeys'
import { toast } from './useToast'
import type { AdminApiKeyRecord, ApiKeyRecord, ApiKeyScope, AuthUser } from '../types'

type ApiKeyManagementProps = {
  currentUser: AuthUser
  users: AuthUser[]
}

const DEFAULT_SCOPES: ApiKeyScope[] = ['video_jobs:create', 'video_jobs:read', 'video_jobs:review']

export const scopeOptions: Array<{ value: ApiKeyScope; label: string; hint: string }> = [
  { value: 'video_jobs:create', label: '创建任务', hint: '允许调用 POST /v1/video-jobs' },
  { value: 'video_jobs:read', label: '查询状态', hint: '允许查询状态、SSE 进度和下载结果' },
  { value: 'video_jobs:review', label: '审核续跑', hint: '允许提交 shot_plan / replication_plan 审核' },
]

function getErrorMessage(error: unknown, fallback: string) {
  const maybeError = error as { userMessage?: string; message?: string } | undefined
  return maybeError?.userMessage || maybeError?.message || fallback
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

export function useApiKeyManagement(props: ApiKeyManagementProps) {
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

  return {
    actingKeyId,
    adminView,
    createKeyForCustomer,
    createOwnKey,
    creatingCustomer,
    creatingMine,
    customerForm,
    customerKeys,
    customerUsers,
    disableCustomerKey,
    disableOwnKey,
    filteredCustomerKeys,
    formatDate,
    loadingCustomers,
    loadingMine,
    myForm,
    myKeys,
    refreshCustomerKeys,
    refreshMineKeys,
    revealedKey,
    scopeOptions,
    selectedCustomer,
    selectedCustomerId,
    toggleScope,
    copyRevealedKey,
  }
}
