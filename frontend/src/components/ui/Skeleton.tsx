import { cn } from '../../lib/utils'

/**
 * Base shimmer block with configurable dimensions and rounded corners.
 */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse bg-slate-200 rounded', className)} />
}

/**
 * Card-shaped skeleton matching the material grid item layout
 * (4:3 thumbnail + two text lines).
 */
export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'rounded-2xl overflow-hidden border border-slate-200 bg-white',
        className,
      )}
    >
      <Skeleton className="aspect-[4/3] rounded-none" />
      <div className="px-3 py-2 space-y-1.5">
        <Skeleton className="h-3 w-3/4 rounded" />
        <Skeleton className="h-2.5 w-1/2 rounded" />
      </div>
    </div>
  )
}

/**
 * Text-line skeletons for lists or descriptions.
 * Renders `lines` shimmer bars with varying widths.
 */
export function SkeletonText({
  lines = 3,
  className,
}: {
  lines?: number
  className?: string
}) {
  const widths = ['w-full', 'w-5/6', 'w-4/6', 'w-3/4', 'w-2/3']
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn('h-3 rounded', widths[i % widths.length])}
        />
      ))}
    </div>
  )
}
