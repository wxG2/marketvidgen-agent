import { useState, useEffect, useRef } from 'react'
import { getTemplates, getChatHistory, sendChatMessage, generatePrompts, getPrompts, updatePrompt } from '../../api/prompts'
import { getSelectedMaterials } from '../../api/materials'
import type { PromptTemplate, ChatMessage, Prompt, MaterialSelection } from '../../types'
import { Send, Loader2, Wand2, Edit3, Check, MessageSquare, Image as ImageIcon, X } from 'lucide-react'
import { cn } from '../../lib/utils'

interface Props {
  projectId: string
}

export default function PromptWorkspace({ projectId }: Props) {
  const [templates, setTemplates] = useState<PromptTemplate[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [prompts, setPrompts] = useState<Prompt[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [streamText, setStreamText] = useState('')
  const [generating, setGenerating] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const [selections, setSelections] = useState<MaterialSelection[]>([])
  const [attachedMaterials, setAttachedMaterials] = useState<MaterialSelection[]>([])
  const chatEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getTemplates().then(setTemplates)
    getChatHistory(projectId).then(setMessages)
    getPrompts(projectId).then(setPrompts)
    getSelectedMaterials(projectId).then(setSelections)
  }, [projectId])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamText])

  const handleSend = async () => {
    if ((!input.trim() && attachedMaterials.length === 0) || streaming) return

    let content = input.trim()
    if (attachedMaterials.length > 0) {
      const materialRefs = attachedMaterials.map((s) => {
        const name = s.material?.filename || '素材'
        const cat = s.category
        return `[素材: ${cat}/${name}]`
      }).join(' ')
      content = content ? `${materialRefs}\n${content}` : materialRefs
    }

    setInput('')
    setAttachedMaterials([])

    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, userMsg])

    setStreaming(true)
    setStreamText('')
    let collected = ''
    await sendChatMessage(projectId, content, (chunk) => {
      collected += chunk
      setStreamText(collected)
    })
    setMessages((prev) => [...prev, {
      id: (Date.now() + 1).toString(),
      role: 'assistant',
      content: collected,
      created_at: new Date().toISOString(),
    }])
    setStreamText('')
    setStreaming(false)
  }

  const handleGenerate = async () => {
    setGenerating(true)
    try {
      const result = await generatePrompts(projectId)
      setPrompts(result)
    } finally {
      setGenerating(false)
    }
  }

  const handleSaveEdit = async (promptId: string) => {
    await updatePrompt(projectId, promptId, editText)
    setPrompts((prev) => prev.map((p) => p.id === promptId ? { ...p, prompt_text: editText } : p))
    setEditingId(null)
  }

  const toggleAttachMaterial = (sel: MaterialSelection) => {
    setAttachedMaterials((prev) => {
      const exists = prev.some((s) => s.material_id === sel.material_id)
      if (exists) return prev.filter((s) => s.material_id !== sel.material_id)
      return [...prev, sel]
    })
  }

  const isAttached = (sel: MaterialSelection) =>
    attachedMaterials.some((s) => s.material_id === sel.material_id)

  const selectionsByCategory = selections.reduce<Record<string, MaterialSelection[]>>((acc, s) => {
    if (!acc[s.category]) acc[s.category] = []
    acc[s.category].push(s)
    return acc
  }, {})

  const renderMessageContent = (content: string) => {
    const materialRefRegex = /\[素材: ([^\]]+)\]/g
    const parts: (string | { ref: string })[] = []
    let lastIndex = 0
    let match
    while ((match = materialRefRegex.exec(content)) !== null) {
      if (match.index > lastIndex) {
        parts.push(content.slice(lastIndex, match.index))
      }
      parts.push({ ref: match[1] })
      lastIndex = match.index + match[0].length
    }
    if (lastIndex < content.length) {
      parts.push(content.slice(lastIndex))
    }

    return (
      <>
        {parts.map((part, i) =>
          typeof part === 'string' ? (
            <span key={i}>{part}</span>
          ) : (
            <span key={i} className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-blue-50 rounded text-blue-600 text-xs mx-0.5">
              <ImageIcon size={10} />
              {part.ref}
            </span>
          )
        )}
      </>
    )
  }

  return (
    <div className="flex h-full">
      {/* Left: Selected materials panel */}
      <div className="w-56 bg-gray-50 border-r border-gray-200 overflow-y-auto shrink-0 flex flex-col">
        <div className="p-3 text-xs text-gray-400 uppercase tracking-wider">已选素材</div>

        {selections.length === 0 ? (
          <div className="flex-1 flex items-center justify-center text-gray-400 text-sm px-4 text-center">
            <p>暂无选中素材，请在上一步选择素材</p>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto">
            {Object.entries(selectionsByCategory).map(([category, sels]) => (
              <div key={category} className="mb-2">
                <div className="px-3 py-1.5 text-xs text-gray-500 font-medium">{category}</div>
                <div className="grid grid-cols-2 gap-1.5 px-2">
                  {sels.map((sel) => {
                    const mat = sel.material
                    const attached = isAttached(sel)
                    return (
                      <button
                        key={sel.material_id}
                        onClick={() => toggleAttachMaterial(sel)}
                        className={cn(
                          'relative rounded-lg overflow-hidden border-2 transition-all aspect-square',
                          attached
                            ? 'border-blue-500 ring-1 ring-blue-500/30'
                            : 'border-transparent hover:border-gray-300',
                        )}
                        title={mat?.filename || '素材'}
                      >
                        <img
                          src={mat?.thumbnail_url || ''}
                          alt={mat?.filename || ''}
                          className="w-full h-full object-cover"
                          loading="lazy"
                        />
                        {attached && (
                          <div className="absolute inset-0 bg-blue-500/20 flex items-center justify-center">
                            <Check size={16} className="text-white drop-shadow" />
                          </div>
                        )}
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Templates section */}
        <div className="border-t border-gray-200">
          <div className="p-3 text-xs text-gray-400 uppercase tracking-wider">模板</div>
          {templates.map((t) => (
            <button
              key={t.name}
              onClick={() => setInput(t.template)}
              className="w-full text-left px-3 py-2 border-b border-gray-100 hover:bg-gray-100 transition-colors"
            >
              <div className="text-xs text-gray-900 font-medium">{t.name}</div>
              <div className="text-[11px] text-gray-400 mt-0.5">{t.description}</div>
            </button>
          ))}

          <div className="p-3">
            <button
              onClick={handleGenerate}
              disabled={generating || selections.length === 0}
              className="w-full px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {generating ? <Loader2 className="animate-spin" size={16} /> : <Wand2 size={16} />}
              生成提示词
            </button>
          </div>
        </div>
      </div>

      {/* Center: Chat */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && !streaming && (
            <div className="text-center text-gray-400 mt-20">
              <MessageSquare size={48} className="mx-auto mb-3" />
              <p>点击左侧素材将其附加到消息中</p>
              <p className="text-sm mt-1">选择模板或直接输入需求，AI 将帮助您生成视频提示词</p>
            </div>
          )}
          {messages.map((msg) => (
            <div key={msg.id} className={cn('flex', msg.role === 'user' ? 'justify-end' : 'justify-start')}>
              <div className={cn(
                'max-w-[80%] rounded-xl px-4 py-2.5 text-sm whitespace-pre-wrap',
                msg.role === 'user' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700',
              )}>
                {renderMessageContent(msg.content)}
              </div>
            </div>
          ))}
          {streaming && streamText && (
            <div className="flex justify-start">
              <div className="max-w-[80%] rounded-xl px-4 py-2.5 text-sm bg-gray-100 text-gray-700 whitespace-pre-wrap">
                {streamText}
                <span className="inline-block w-1.5 h-4 bg-blue-500 ml-0.5 animate-pulse" />
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Attached materials preview */}
        {attachedMaterials.length > 0 && (
          <div className="px-4 py-2 border-t border-gray-200 flex items-center gap-2 flex-wrap">
            <span className="text-xs text-gray-400">附加素材:</span>
            {attachedMaterials.map((sel) => (
              <div
                key={sel.material_id}
                className="relative group inline-flex items-center gap-1 px-1.5 py-0.5 bg-blue-50 rounded text-xs text-blue-600"
              >
                <img
                  src={sel.material?.thumbnail_url || ''}
                  alt=""
                  className="w-5 h-5 rounded object-cover"
                />
                <span className="max-w-[80px] truncate">{sel.material?.filename || '素材'}</span>
                <button
                  onClick={() => toggleAttachMaterial(sel)}
                  className="ml-0.5 text-gray-400 hover:text-red-500"
                >
                  <X size={10} />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Input */}
        <div className="p-4 border-t border-gray-200">
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
              placeholder={attachedMaterials.length > 0 ? '描述想要的效果（可选）...' : '输入您的需求...'}
              className="flex-1 bg-gray-50 text-gray-900 rounded-lg px-4 py-2.5 text-sm border border-gray-300 focus:border-blue-500 focus:outline-none"
            />
            <button
              onClick={handleSend}
              disabled={(!input.trim() && attachedMaterials.length === 0) || streaming}
              className="px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-50"
            >
              <Send size={18} />
            </button>
          </div>
        </div>
      </div>

      {/* Right: Bound cards (material + prompt) */}
      {prompts.length > 0 && (
        <div className="w-80 bg-gray-50 border-l border-gray-200 overflow-y-auto shrink-0">
          <div className="p-3 text-xs text-gray-400 uppercase tracking-wider">
            素材绑定 ({prompts.length})
          </div>
          <p className="px-3 pb-2 text-[11px] text-gray-400">每个素材已绑定一条提示词，可编辑后进入下一步生成视频</p>
          {prompts.map((p, i) => {
            const matchedSel = selections.find((s) => s.id === p.material_selection_id)
            return (
              <div key={p.id} className="mx-3 mb-3 bg-white rounded-lg overflow-hidden border border-gray-200">
                <div className="flex items-center gap-2 p-2 bg-gray-50">
                  {matchedSel?.material?.thumbnail_url ? (
                    <img
                      src={matchedSel.material.thumbnail_url}
                      alt=""
                      className="w-10 h-10 rounded object-cover"
                    />
                  ) : (
                    <div className="w-10 h-10 rounded bg-gray-200 flex items-center justify-center">
                      <ImageIcon size={16} className="text-gray-400" />
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <span className="text-xs text-blue-600 block">绑定 #{i + 1}</span>
                    {matchedSel && (
                      <span className="text-[11px] text-gray-400 truncate block">
                        {matchedSel.category} / {matchedSel.material?.filename}
                      </span>
                    )}
                  </div>
                  {editingId === p.id ? (
                    <button onClick={() => handleSaveEdit(p.id)} className="text-green-500 hover:text-green-600 p-1">
                      <Check size={14} />
                    </button>
                  ) : (
                    <button onClick={() => { setEditingId(p.id); setEditText(p.prompt_text) }} className="text-gray-400 hover:text-gray-700 p-1">
                      <Edit3 size={14} />
                    </button>
                  )}
                </div>
                <div className="p-2">
                  {editingId === p.id ? (
                    <textarea
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      className="w-full bg-gray-50 text-gray-700 text-xs rounded p-2 border border-gray-300 focus:border-blue-500 focus:outline-none resize-none"
                      rows={4}
                      autoFocus
                    />
                  ) : (
                    <p className="text-xs text-gray-500 leading-relaxed">{p.prompt_text}</p>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
