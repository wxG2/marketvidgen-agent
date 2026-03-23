import { cn } from '../../lib/utils'
import { Upload, Brain, Image, MessageSquare, Wand2, CheckSquare, Film } from 'lucide-react'

const steps = [
  { num: 1, label: '上传视频', icon: Upload },
  { num: 2, label: '视频理解', icon: Brain },
  { num: 3, label: '素材选择', icon: Image },
  { num: 4, label: '提示词生成', icon: MessageSquare },
  { num: 5, label: '视频生成', icon: Wand2 },
  { num: 6, label: '视频选择', icon: CheckSquare },
  { num: 7, label: '时间轴编辑', icon: Film },
]

interface Props {
  currentStep: number
  onStepClick: (step: number) => void
  maxStep: number
}

export default function WorkflowStepper({ currentStep, onStepClick, maxStep }: Props) {
  return (
    <div className="flex items-center gap-1 px-4 py-3 bg-gray-50 border-b border-gray-200 overflow-x-auto">
      {steps.map((step, i) => {
        const Icon = step.icon
        const isActive = step.num === currentStep
        const isCompleted = step.num < currentStep
        const isClickable = step.num <= maxStep
        return (
          <div key={step.num} className="flex items-center">
            {i > 0 && <div className={cn('w-6 h-px mx-1', isCompleted ? 'bg-blue-400' : 'bg-gray-300')} />}
            <button
              onClick={() => isClickable && onStepClick(step.num)}
              disabled={!isClickable}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm whitespace-nowrap transition-all',
                isActive && 'bg-blue-600 text-white shadow-sm',
                isCompleted && !isActive && 'bg-blue-50 text-blue-700 hover:bg-blue-100',
                !isActive && !isCompleted && isClickable && 'text-gray-600 hover:bg-gray-100',
                !isClickable && 'text-gray-400 cursor-not-allowed',
              )}
            >
              <Icon size={16} />
              <span className="hidden sm:inline">{step.label}</span>
            </button>
          </div>
        )
      })}
    </div>
  )
}
