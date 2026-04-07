import type { ReactNode } from 'react'
import WorkflowStepper from './WorkflowStepper'
import { Archive, BarChart3, LogOut } from 'lucide-react'
import CapyAvatar from '../ui/CapyAvatar'

interface Props {
  projectName: string
  currentStep: number
  maxStep: number
  currentUserName?: string
  onStepClick?: ((step: number) => void) | undefined
  onOpenDashboard?: (() => void) | undefined
  onOpenRepository?: (() => void) | undefined
  onLogout?: (() => void) | undefined
  centerSlot?: ReactNode
  children: ReactNode
}

export default function AppShell({ projectName, currentStep, maxStep, currentUserName, onStepClick, onOpenDashboard, onOpenRepository, onLogout, centerSlot, children }: Props) {
  return (
    <div className="h-screen flex flex-col bg-[linear-gradient(180deg,#f8f0e1_0%,#efe5d0_100%)]">
      <header className="relative flex items-center justify-between border-b border-[#d9ccb5] bg-[#fff9ef]/92 px-4 py-3 backdrop-blur">
        <div className="flex items-center gap-2">
          <CapyAvatar size="sm" className="border-[#d3c2a1] bg-[#faf2df]" />
          <div>
            <span className="text-lg font-semibold text-[#4c3b22]">capy</span>
            <div className="text-[11px] uppercase tracking-[0.2em] text-[#9a8660]">Capybara Studio</div>
          </div>
        </div>
        {centerSlot && (
          <div className="absolute left-1/2 -translate-x-1/2 flex items-center gap-2">
            {centerSlot}
          </div>
        )}
        <div className="flex items-center gap-3">
          {onOpenRepository && (
            <button
              onClick={onOpenRepository}
              className="inline-flex items-center gap-2 rounded-full border border-[#d5c4a4] bg-[#f7efdd] px-3 py-1.5 text-sm text-[#6e5a38] hover:bg-[#f1e5ce]"
            >
              <Archive size={14} />
              仓库
            </button>
          )}
          {onOpenDashboard && (
            <button
              onClick={onOpenDashboard}
              className="inline-flex items-center gap-2 rounded-full border border-[#d5c4a4] bg-[#f7efdd] px-3 py-1.5 text-sm text-[#6e5a38] hover:bg-[#f1e5ce]"
            >
              <BarChart3 size={14} />
              仪表盘
            </button>
          )}
          {currentUserName && (
            <span className="text-sm text-[#7c6845]">{currentUserName}</span>
          )}
          {onLogout && (
            <button
              onClick={onLogout}
              className="inline-flex items-center gap-2 rounded-full border border-[#d5c4a4] bg-[#fff8ee] px-3 py-1.5 text-sm text-[#6e5a38] hover:bg-[#f4ead7]"
            >
              <LogOut size={14} />
              退出
            </button>
          )}
          <span className="rounded-full bg-[#efe2c7] px-3 py-1 text-sm text-[#6d5936]">{projectName}</span>
        </div>
      </header>
      {onStepClick && (
        <WorkflowStepper currentStep={currentStep} onStepClick={onStepClick} maxStep={maxStep} />
      )}
      <main className="flex-1 overflow-hidden">{children}</main>
    </div>
  )
}
