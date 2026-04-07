import { useState } from 'react'
import { LogIn, UserPlus } from 'lucide-react'
import { login, register } from '../../api/auth'
import type { AuthUser } from '../../types'
import { useToast } from '../ui/Toast'
import CapyAvatar from '../ui/CapyAvatar'

export default function AuthPage({ onAuthenticated }: { onAuthenticated: (user: AuthUser) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const { toast } = useToast()

  const handleSubmit = async () => {
    if (!username.trim() || !password.trim()) return
    setSubmitting(true)
    try {
      const user = mode === 'login'
        ? await login({ username: username.trim(), password })
        : await register({ username: username.trim(), password })
      onAuthenticated(user)
    } catch (error: any) {
      toast('error', error?.userMessage || error?.response?.data?.detail || '认证失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.18),_transparent_35%),linear-gradient(180deg,#f8fafc_0%,#e2e8f0_100%)] flex items-center justify-center p-6">
      <div className="w-full max-w-md rounded-[28px] border border-slate-200 bg-white/90 p-8 shadow-xl backdrop-blur">
        <div className="text-center">
          <CapyAvatar size="lg" className="mx-auto border-slate-300" />
          <h1 className="mt-4 text-3xl font-semibold text-slate-900">capy</h1>
          <p className="mt-2 text-sm text-slate-500">登录后进入你的项目、模板与自动生成工作台</p>
        </div>

        <div className="mt-8 flex rounded-full bg-slate-100 p-1">
          <button
            onClick={() => setMode('login')}
            className={`flex-1 rounded-full px-4 py-2 text-sm ${mode === 'login' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500'}`}
          >
            登录
          </button>
          <button
            onClick={() => setMode('register')}
            className={`flex-1 rounded-full px-4 py-2 text-sm ${mode === 'register' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500'}`}
          >
            注册
          </button>
        </div>

        <div className="mt-6 space-y-4">
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="用户名"
            className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none focus:border-blue-500"
          />
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
            type="password"
            placeholder="密码"
            className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none focus:border-blue-500"
          />
          <button
            disabled={submitting || !username.trim() || !password.trim()}
            onClick={handleSubmit}
            className="w-full rounded-2xl bg-blue-600 px-4 py-3 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {mode === 'login' ? <LogIn size={16} /> : <UserPlus size={16} />}
            {mode === 'login' ? '登录进入工作台' : '创建账号并进入'}
          </button>
        </div>
      </div>
    </div>
  )
}
