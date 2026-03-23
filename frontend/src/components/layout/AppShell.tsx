import type { ReactNode } from 'react'
import WorkflowStepper from './WorkflowStepper'
import { BarChart3, Film } from 'lucide-react'

interface Props {
  projectName: string
  currentStep: number
  maxStep: number
  onStepClick?: ((step: number) => void) | undefined
  onOpenDashboard?: (() => void) | undefined
  children: ReactNode
}

export default function AppShell({ projectName, currentStep, maxStep, onStepClick, onOpenDashboard, children }: Props) {
  return (
    <div className="h-screen flex flex-col bg-white">
      <header className="flex items-center justify-between px-4 py-2 bg-white border-b border-gray-200">
        <div className="flex items-center gap-2">
          <Film className="text-gray-800" size={24} />
          <span className="text-lg font-semibold text-gray-900">VidGen</span>
        </div>
        <div className="flex items-center gap-3">
          {onOpenDashboard && (
            <button
              onClick={onOpenDashboard}
              className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
            >
              <BarChart3 size={14} />
              仪表盘
            </button>
          )}
          <span className="text-sm text-gray-500">{projectName}</span>
        </div>
      </header>
      {onStepClick && (
        <WorkflowStepper currentStep={currentStep} onStepClick={onStepClick} maxStep={maxStep} />
      )}
      <main className="flex-1 overflow-hidden">{children}</main>
    </div>
  )
}
