import { useEffect, useMemo, useState } from 'react'
import {
  BriefcaseBusiness,
  Building2,
  Cpu,
  Leaf,
  Loader2,
  PlusCircle,
  Save,
  Sparkles,
  Store,
  Trash2,
  Wand2,
} from 'lucide-react'
import {
  createBackgroundTemplate,
  deleteBackgroundTemplate,
  generateBackgroundTemplateFromKeywords,
  getBackgroundTemplateLearningLogs,
  importPresetBackgroundTemplates,
  listBackgroundTemplates,
  updateBackgroundTemplate,
} from '../../api/backgroundTemplates'
import { listUsers, updateUser } from '../../api/auth'
import type {
  AuthUser,
  BackgroundTemplate,
  BackgroundTemplateKeywordDraft,
  BackgroundTemplateLearningLog,
} from '../../types'
import { cn } from '../../lib/utils'
import { useToast } from '../ui/Toast'
import CapyAvatar from '../ui/CapyAvatar'

const TEMPLATE_META: Record<string, { icon: typeof Cpu; accent: string; tint: string }> = {
  '科技博主': { icon: Cpu, accent: 'text-sky-700', tint: 'bg-sky-100' },
  '大健康招商博主': { icon: Leaf, accent: 'text-emerald-700', tint: 'bg-emerald-100' },
  '本地生活探店博主': { icon: Store, accent: 'text-amber-700', tint: 'bg-amber-100' },
  '品牌创始人 IP': { icon: BriefcaseBusiness, accent: 'text-stone-700', tint: 'bg-stone-200' },
}

const DETAIL_FIELDS: Array<{ key: keyof BackgroundTemplateKeywordDraft; label: string }> = [
  { key: 'brand_info', label: '品牌信息' },
  { key: 'user_requirements', label: '用户需求' },
  { key: 'character_name', label: '角色名称' },
  { key: 'identity', label: '角色身份' },
  { key: 'scene_context', label: '场景背景' },
  { key: 'tone_style', label: '语气风格' },
  { key: 'visual_style', label: '视觉风格' },
  { key: 'do_not_include', label: '避免内容' },
  { key: 'notes', label: '补充备注' },
]

function toDraft(template: BackgroundTemplate): BackgroundTemplateKeywordDraft {
  return {
    name: template.name,
    brand_info: template.brand_info,
    user_requirements: template.user_requirements,
    character_name: template.character_name,
    identity: template.identity,
    scene_context: template.scene_context,
    tone_style: template.tone_style,
    visual_style: template.visual_style,
    do_not_include: template.do_not_include,
    notes: template.notes,
  }
}

function buildCompiledContext(draft: BackgroundTemplateKeywordDraft | null) {
  if (!draft) return ''
  const sections: Array<[string, string | null | undefined]> = [
    ['品牌信息', draft.brand_info],
    ['用户需求', draft.user_requirements],
    ['角色名称', draft.character_name],
    ['角色身份', draft.identity],
    ['场景背景', draft.scene_context],
    ['语气风格', draft.tone_style],
    ['视觉风格', draft.visual_style],
    ['避免内容', draft.do_not_include],
    ['备注', draft.notes],
  ]
  return sections
    .filter(([, value]) => value && value.trim())
    .map(([label, value]) => `${label}：${value?.trim()}`)
    .join('\n')
}

function templateMeta(name: string) {
  if (TEMPLATE_META[name]) return TEMPLATE_META[name]
  return { icon: Building2, accent: 'text-stone-700', tint: 'bg-stone-100' }
}

export default function PersonalCenterPage({ currentUser }: { currentUser: AuthUser }) {
  const [templates, setTemplates] = useState<BackgroundTemplate[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [logs, setLogs] = useState<BackgroundTemplateLearningLog[]>([])
  const [users, setUsers] = useState<AuthUser[]>([])
  const [keywords, setKeywords] = useState('')
  const [draft, setDraft] = useState<BackgroundTemplateKeywordDraft | null>(null)
  const [generatingDraft, setGeneratingDraft] = useState(false)
  const [savingDraft, setSavingDraft] = useState(false)
  const { toast } = useToast()

  const selectedTemplate = templates.find((item) => item.id === selectedId) || null
  const previewDraft = draft || (selectedTemplate ? toDraft(selectedTemplate) : null)
  const compiledPreview = buildCompiledContext(previewDraft)

  const loadTemplates = async () => {
    const items = await listBackgroundTemplates()
    setTemplates(items)
    setSelectedId((prev) => prev && items.some((item) => item.id === prev) ? prev : items[0]?.id || null)
  }

  useEffect(() => {
    loadTemplates().catch(() => toast('error', '加载角色模板失败'))
  }, [])

  useEffect(() => {
    setDraft(null)
    if (!selectedTemplate) {
      setLogs([])
      return
    }
    getBackgroundTemplateLearningLogs(selectedTemplate.id).then(setLogs).catch(() => setLogs([]))
  }, [selectedTemplate?.id])

  useEffect(() => {
    if (currentUser.role !== 'admin') return
    listUsers().then(setUsers).catch(() => setUsers([]))
  }, [currentUser.role])

  const handleGenerate = async () => {
    if (!keywords.trim()) {
      toast('warning', '先输入关键词，再让 capy 帮你生成角色背景信息')
      return
    }
    setGeneratingDraft(true)
    try {
      const generated = await generateBackgroundTemplateFromKeywords({
        keywords: keywords.trim(),
        template_id: selectedTemplate?.id,
      })
      setDraft(generated)
      toast('success', '角色背景草稿已生成')
    } catch (error: any) {
      toast('error', error?.userMessage || '角色背景生成失败')
    } finally {
      setGeneratingDraft(false)
    }
  }

  const handleSave = async (mode: 'update' | 'create') => {
    if (!draft) return
    setSavingDraft(true)
    try {
      if (mode === 'update' && selectedTemplate) {
        const updated = await updateBackgroundTemplate(selectedTemplate.id, draft)
        setTemplates((prev) => prev.map((item) => item.id === updated.id ? updated : item))
        setDraft(null)
        toast('success', '角色背景已更新')
      } else {
        const created = await createBackgroundTemplate(draft)
        setTemplates((prev) => [created, ...prev])
        setSelectedId(created.id)
        setDraft(null)
        toast('success', '新角色已创建')
      }
    } catch (error: any) {
      toast('error', error?.userMessage || '保存角色背景失败')
    } finally {
      setSavingDraft(false)
    }
  }

  const handleDelete = async () => {
    if (!selectedTemplate) return
    try {
      await deleteBackgroundTemplate(selectedTemplate.id)
      const next = templates.filter((item) => item.id !== selectedTemplate.id)
      setTemplates(next)
      setSelectedId(next[0]?.id || null)
      setDraft(null)
      toast('success', '角色模板已删除')
    } catch (error: any) {
      toast('error', error?.userMessage || '删除角色模板失败')
    }
  }

  const importPresets = async () => {
    try {
      const created = await importPresetBackgroundTemplates()
      await loadTemplates()
      toast('success', created.length > 0 ? `已导入 ${created.length} 个预设角色` : '预设角色已经都在角色库里了')
    } catch (error: any) {
      toast('error', error?.userMessage || '导入预设角色失败')
    }
  }

  const roleCards = useMemo(
    () => templates.map((template) => ({ ...template, meta: templateMeta(template.name) })),
    [templates],
  )

  return (
    <div className="space-y-6">
      <section className="rounded-[34px] border border-[#d9cdb8] bg-[radial-gradient(circle_at_top_right,rgba(175,198,127,0.28),transparent_32%),linear-gradient(135deg,#fbf4e7_0%,#f2ead7_56%,#e8dfc8_100%)] p-6 shadow-[0_24px_80px_rgba(120,90,43,0.10)]">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-[#d8c8ab] bg-white/70 px-3 py-1 text-xs uppercase tracking-[0.22em] text-[#8c6d3e]">
              <CapyAvatar size="sm" className="border-[#cbb890] bg-[#f7f0df]" />
              Capy Persona Lab
            </div>
            <h2 className="mt-4 text-3xl font-semibold text-[#4b3b22]">让角色像卡皮巴拉一样稳定、温和、可信</h2>
            <p className="mt-3 text-sm leading-7 text-[#6e5a39]">
              先选一个角色原型，再输入关键词。capy 会自动补全品牌定位、使用场景、表达语气和视觉风格。个人中心只展示当前选中的角色背景，方便你快速确认。
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr),auto] lg:min-w-[480px]">
            <div className="rounded-[28px] border border-[#dcccb0] bg-white/78 p-4 shadow-sm">
              <div className="flex items-center gap-2 text-sm font-medium text-[#5d4826]">
                <Wand2 size={16} className="text-[#8c6d3e]" />
                关键词生成角色背景
              </div>
              <div className="mt-2 text-xs leading-6 text-[#7a6747]">
                例子：科技测评、极简桌搭、女创始人口播、口腔诊所、银发健康、城市探店、养生招商
              </div>
              <textarea
                rows={3}
                value={keywords}
                onChange={(e) => setKeywords(e.target.value)}
                placeholder="输入你想要的关键词，比如：科技测评、极简桌搭、理性种草、创始人口播"
                className="mt-3 w-full rounded-[22px] border border-[#dfd3bc] bg-[#fffaf1] px-4 py-3 text-sm text-[#4b3b22] outline-none transition focus:border-[#b59a69] focus:ring-2 focus:ring-[#dbc79c]"
              />
            </div>

            <div className="flex flex-col gap-3">
              <button
                onClick={handleGenerate}
                disabled={generatingDraft}
                className="inline-flex items-center justify-center gap-2 rounded-full bg-[#7e9d53] px-5 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-[#718f47] disabled:opacity-50"
              >
                {generatingDraft ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
                生成背景草稿
              </button>
              <button
                onClick={() => {
                  setSelectedId(null)
                  setDraft(null)
                }}
                className="inline-flex items-center justify-center gap-2 rounded-full border border-[#d3c2a1] bg-white/80 px-5 py-3 text-sm font-medium text-[#6c5632] hover:bg-[#fff6e8]"
              >
                <PlusCircle size={16} />
                新建角色
              </button>
              <button
                onClick={importPresets}
                className="inline-flex items-center justify-center gap-2 rounded-full border border-[#d3c2a1] bg-[#f6efe0] px-5 py-3 text-sm font-medium text-[#6c5632] hover:bg-[#f0e5cf]"
              >
                <Store size={16} />
                导入预设
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[360px,minmax(0,1fr)]">
        <aside className="rounded-[30px] border border-[#ddd0bb] bg-white/82 p-5 shadow-sm backdrop-blur">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-[#9a8863]">角色库</div>
              <div className="mt-2 text-sm leading-6 text-[#715d3a]">其他预设通过图标浏览，点一下就能切换当前角色。</div>
            </div>
            <CapyAvatar size="md" className="border-[#d3c2a1] bg-[#fbf3e1]" />
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3">
            {roleCards.map((template) => {
              const Icon = template.meta.icon
              const active = template.id === selectedId
              return (
                <button
                  key={template.id}
                  onClick={() => setSelectedId(template.id)}
                  className={cn(
                    'rounded-[24px] border p-4 text-left transition-all',
                    active
                      ? 'border-[#b59a69] bg-[#f6ebd4] shadow-[0_18px_32px_rgba(144,109,54,0.12)]'
                      : 'border-[#e4d8c4] bg-[#fffaf1] hover:border-[#cbb890] hover:bg-[#fbf1df]',
                  )}
                >
                  <div className={cn('inline-flex h-11 w-11 items-center justify-center rounded-2xl', template.meta.tint)}>
                    <Icon size={20} className={template.meta.accent} />
                  </div>
                  <div className="mt-3 text-sm font-medium text-[#4f3f25]">{template.name}</div>
                  <div className="mt-1 text-xs leading-5 text-[#8a7754]">
                    学习 {template.learning_count} 次
                    {template.updated_by === 'agent' ? ' · Agent 更新' : ' · 用户确认'}
                  </div>
                </button>
              )
            })}
          </div>

          {templates.length === 0 && (
            <div className="mt-4 rounded-[24px] border border-dashed border-[#d6c6aa] bg-[#fdf7ea] px-4 py-6 text-sm text-[#7f6a46]">
              还没有角色模板。输入关键词后，capy 可以直接帮你生成第一版角色背景。
            </div>
          )}
        </aside>

        <section className="space-y-6">
          <div className="rounded-[30px] border border-[#ddd0bb] bg-white/88 p-6 shadow-sm backdrop-blur">
            <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
              <div>
                <div className="inline-flex items-center gap-2 rounded-full bg-[#f6ecd8] px-3 py-1 text-xs uppercase tracking-[0.18em] text-[#8b6f42]">
                  当前角色背景
                </div>
                <h3 className="mt-3 text-2xl font-semibold text-[#4d3d24]">
                  {previewDraft?.name || selectedTemplate?.name || '先选择一个角色，或者直接生成新的背景草稿'}
                </h3>
                <p className="mt-2 text-sm leading-7 text-[#74603d]">
                  {draft
                    ? '这是 capy 根据关键词为你生成的最新草稿。确认没问题后直接保存即可。'
                    : selectedTemplate
                      ? '这里只展示你当前选中的角色背景信息，便于在进入自动模式前最后确认一次。'
                      : '你还没有选中角色。可以从左边点一个预设，或者直接输入关键词生成全新的角色背景。'}
                </p>
              </div>

              <div className="flex flex-wrap gap-3">
                {draft && selectedTemplate && (
                  <button
                    onClick={() => handleSave('update')}
                    disabled={savingDraft}
                    className="inline-flex items-center gap-2 rounded-full bg-[#7e9d53] px-4 py-2 text-sm font-medium text-white hover:bg-[#718f47] disabled:opacity-50"
                  >
                    {savingDraft ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
                    更新当前角色
                  </button>
                )}
                {draft && (
                  <button
                    onClick={() => handleSave('create')}
                    disabled={savingDraft}
                    className="inline-flex items-center gap-2 rounded-full border border-[#cdb88e] bg-[#fff4df] px-4 py-2 text-sm font-medium text-[#6d5830] hover:bg-[#faedd1] disabled:opacity-50"
                  >
                    <PlusCircle size={15} />
                    另存为新角色
                  </button>
                )}
                {draft && (
                  <button
                    onClick={() => setDraft(null)}
                    className="inline-flex items-center gap-2 rounded-full border border-[#ddd0bb] bg-white px-4 py-2 text-sm text-[#796543] hover:bg-[#f9f1e3]"
                  >
                    放弃草稿
                  </button>
                )}
                {selectedTemplate && !draft && (
                  <button
                    onClick={handleDelete}
                    className="inline-flex items-center gap-2 rounded-full border border-[#ead8ca] bg-[#fff4ef] px-4 py-2 text-sm text-[#b3583f] hover:bg-[#ffe7dd]"
                  >
                    <Trash2 size={15} />
                    删除角色
                  </button>
                )}
              </div>
            </div>

            <div className="mt-6 rounded-[28px] border border-[#e2d6c1] bg-[linear-gradient(180deg,#fffaf1_0%,#f7efdf_100%)] p-5">
              <div className="text-sm font-semibold text-[#534226]">编译后的背景信息</div>
              <div className="mt-3 whitespace-pre-wrap rounded-[22px] border border-[#e4d8c4] bg-white/70 p-4 text-sm leading-7 text-[#5f4c2b]">
                {compiledPreview || '当前还没有完整的角色背景信息。你可以先输入关键词，让 capy 自动补全。'}
              </div>
            </div>

            {previewDraft && (
              <div className="mt-5 grid gap-3 md:grid-cols-2">
                {DETAIL_FIELDS.map(({ key, label }) => {
                  const value = previewDraft[key]
                  if (!value) return null
                  return (
                    <div key={key} className="rounded-[22px] border border-[#eadfcb] bg-[#fffaf1] px-4 py-4">
                      <div className="text-xs uppercase tracking-[0.16em] text-[#a08d67]">{label}</div>
                      <div className="mt-2 text-sm leading-6 text-[#5d4a2a]">{value}</div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {selectedTemplate && (
            <div className="rounded-[30px] border border-[#ddd0bb] bg-white/88 p-6 shadow-sm">
              <div className="text-sm font-semibold text-[#4f3f25]">最近学习记录</div>
              <div className="mt-3 space-y-3">
                {logs.length === 0 && <div className="text-sm text-[#856f4b]">这个角色还没有学习记录。</div>}
                {logs.map((log) => (
                  <div key={log.id} className="rounded-[22px] border border-[#e6dbc8] bg-[#fff8ed] p-4">
                    <div className="text-xs text-[#9e8a65]">{new Date(log.created_at).toLocaleString()}</div>
                    <div className="mt-2 text-sm leading-6 text-[#5f4c2b]">{log.summary || '本次没有提炼出新的长期偏好。'}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {currentUser.role === 'admin' && (
            <div className="rounded-[30px] border border-[#ddd0bb] bg-white/88 p-6 shadow-sm">
              <div className="text-sm font-semibold text-[#4f3f25]">账号管理</div>
              <div className="mt-4 space-y-3">
                {users.map((user) => (
                  <div key={user.id} className="flex items-center justify-between rounded-[22px] border border-[#e7dbc8] bg-[#fff8ed] px-4 py-3">
                    <div>
                      <div className="text-sm font-medium text-[#4f3f25]">{user.username}</div>
                      <div className="text-xs text-[#8b7651]">{user.role} · {user.is_active ? '启用中' : '已禁用'}</div>
                    </div>
                    <button
                      onClick={async () => {
                        const next = await updateUser(user.id, { is_active: !user.is_active })
                        setUsers((prev) => prev.map((item) => item.id === next.id ? next : item))
                      }}
                      className="rounded-full border border-[#d8c7a4] bg-white px-3 py-1.5 text-xs text-[#6c5632] hover:bg-[#fdf2dd]"
                    >
                      {user.is_active ? '禁用' : '启用'}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      </section>
    </div>
  )
}
