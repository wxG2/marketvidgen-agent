import { cn } from '../../lib/utils'

interface Props {
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const SIZE_MAP: Record<NonNullable<Props['size']>, string> = {
  sm: 'h-8 w-8',
  md: 'h-10 w-10',
  lg: 'h-14 w-14',
}

export default function CapyAvatar({ size = 'md', className }: Props) {
  return (
    <img
      src="/capy-avatar.svg"
      alt="capy avatar"
      className={cn('rounded-2xl border border-slate-200 bg-white object-cover shadow-sm', SIZE_MAP[size], className)}
    />
  )
}
