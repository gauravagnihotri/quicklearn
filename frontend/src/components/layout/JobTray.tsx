import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2, ChevronDown, Loader2, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { api } from '../../lib/api'
import { useJobs } from '../../lib/store'
import { cn, jobLabel } from '../../lib/utils'
import { Progress, Tooltip } from '../ui'

/**
 * Live job progress in the sidebar.
 *
 * PRD §3.3 requires real stage names, never an indefinite spinner. Child jobs are nested under
 * their parent so an ingest reads as one operation with visible steps rather than eight
 * unrelated rows.
 */
export function JobTray() {
  const jobs = useJobs((state) => state.jobs)
  const clearFinished = useJobs((state) => state.clearFinished)
  const [expanded, setExpanded] = useState<number | null>(null)
  const queryClient = useQueryClient()

  const cancel = useMutation({
    mutationFn: (id: number) => api.cancelJob(id),
    onSuccess: () => queryClient.invalidateQueries(),
  })
  const retry = useMutation({
    mutationFn: (id: number) => api.retryJob(id),
    onSuccess: () => queryClient.invalidateQueries(),
  })

  const { roots, childrenOf } = useMemo(() => {
    const all = Object.values(jobs)
    const byParent = new Map<number, typeof all>()
    for (const job of all) {
      if (job.parent_id == null) continue
      const list = byParent.get(job.parent_id) ?? []
      list.push(job)
      byParent.set(job.parent_id, list)
    }
    const active = all
      .filter((job) => job.parent_id == null)
      .filter((job) => job.status === 'running' || job.status === 'queued' || job.status === 'failed')
      .sort((a, b) => b.id - a.id)
      .slice(0, 4)
    return { roots: active, childrenOf: byParent }
  }, [jobs])

  const finishedCount = Object.values(jobs).filter(
    (job) => job.parent_id == null && job.status === 'succeeded',
  ).length

  if (roots.length === 0) {
    if (finishedCount === 0) return null
    return (
      <button
        onClick={clearFinished}
        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-muted)] hover:bg-[var(--bg-sunken)]"
      >
        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
        {finishedCount} finished
        <X className="ml-auto h-3 w-3" />
      </button>
    )
  }

  return (
    <div className="space-y-1.5">
      {roots.map((job) => {
        const children = (childrenOf.get(job.id) ?? []).sort((a, b) => a.id - b.id)
        const open = expanded === job.id
        const failed = job.status === 'failed'
        return (
          <div key={job.id} className="rounded-lg bg-[var(--bg-sunken)] p-2">
            <div className="flex items-center gap-1.5">
              {failed ? (
                <AlertCircle className="h-3.5 w-3.5 shrink-0 text-red-500" />
              ) : (
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--accent)]" />
              )}
              <span className="min-w-0 flex-1 truncate text-[11px] font-medium">
                {job.stage || jobLabel(job.type)}
              </span>
              {children.length > 0 && (
                <button
                  onClick={() => setExpanded(open ? null : job.id)}
                  className="rounded p-0.5 hover:bg-[var(--border)]"
                  aria-label="Toggle steps"
                >
                  <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} />
                </button>
              )}
              <Tooltip label={failed ? 'Retry' : 'Cancel'}>
                <button
                  onClick={() => (failed ? retry.mutate(job.id) : cancel.mutate(job.id))}
                  className="rounded p-0.5 text-[10px] hover:bg-[var(--border)]"
                >
                  {failed ? '↻' : <X className="h-3 w-3" />}
                </button>
              </Tooltip>
            </div>

            {!failed && <Progress value={job.progress} className="mt-1.5 h-1" />}

            {failed && job.error && (
              <p className="mt-1 line-clamp-3 text-[10px] leading-snug text-red-500">{job.error}</p>
            )}

            {open && children.length > 0 && (
              <ul className="mt-2 space-y-1 border-l border-[var(--border)] pl-2">
                {children.map((child) => (
                  <li key={child.id} className="flex items-center gap-1.5 text-[10px]">
                    <StatusDot status={child.status} />
                    <span
                      className={cn(
                        'flex-1 truncate',
                        child.status === 'succeeded' && 'text-[var(--text-muted)] line-through',
                        child.status === 'cancelled' && 'text-[var(--text-muted)] italic',
                      )}
                    >
                      {jobLabel(child.type)}
                    </span>
                    {child.status === 'running' && (
                      <span className="tabular-nums text-[var(--text-muted)]">{child.progress}%</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )
      })}
    </div>
  )
}

function StatusDot({ status }: { status: string }) {
  const colour =
    status === 'succeeded'
      ? 'bg-emerald-500'
      : status === 'failed'
        ? 'bg-red-500'
        : status === 'running'
          ? 'bg-[var(--accent)] animate-pulse'
          : status === 'cancelled'
            ? 'bg-[var(--border)]'
            : 'bg-[var(--text-muted)]/40'
  return <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', colour)} />
}
