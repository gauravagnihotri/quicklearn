import { EyeOff, Star } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { usePlayer } from '../../lib/store'
import type { Segment } from '../../lib/types'
import { cn } from '../../lib/utils'
import { Badge, Switch, Tooltip } from '../ui'

/**
 * Transcript with playback sync and click-to-seek.
 *
 * Low-relevance segments are dimmed rather than hidden by default. They were excluded from
 * generation, not deleted — the source is never destroyed (PRD §6.4) — and seeing what the
 * filter set aside is how a user decides whether to trust it.
 */
export function TranscriptPanel({
  segments,
  highlightIds,
  onSegmentClick,
  scrollToSegment,
}: {
  segments: Segment[]
  /** Segments to emphasise, e.g. the ones behind a clicked note block or chat citation. */
  highlightIds?: number[]
  onSegmentClick?: (segment: Segment) => void
  scrollToSegment?: number | null
}) {
  const { currentMs, seekTo } = usePlayer()
  const [hideFiltered, setHideFiltered] = useState(false)
  const [followPlayback, setFollowPlayback] = useState(true)
  const listRef = useRef<HTMLDivElement>(null)
  const activeRef = useRef<HTMLButtonElement>(null)

  const highlighted = useMemo(() => new Set(highlightIds ?? []), [highlightIds])
  const timed = segments.some((segment) => segment.start_ms !== null)

  const activeId = useMemo(() => {
    if (!timed) return null
    let found: number | null = null
    for (const segment of segments) {
      if (segment.start_ms !== null && segment.start_ms <= currentMs) found = segment.id
      else break
    }
    return found
  }, [segments, currentMs, timed])

  // Keep the playing line in view, but only while the user isn't scrolling elsewhere.
  useEffect(() => {
    if (!followPlayback || !activeRef.current) return
    activeRef.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [activeId, followPlayback])

  useEffect(() => {
    if (!scrollToSegment) return
    const element = listRef.current?.querySelector<HTMLElement>(`[data-segment="${scrollToSegment}"]`)
    element?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [scrollToSegment])

  const visible = hideFiltered ? segments.filter((segment) => segment.relevance >= 0.3) : segments
  const filteredCount = segments.length - segments.filter((s) => s.relevance >= 0.3).length

  if (segments.length === 0) {
    return (
      <p className="px-4 py-8 text-center text-sm text-[var(--text-muted)]">
        No transcript yet.
      </p>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-[var(--border)] px-3 py-2 text-[11px] text-[var(--text-muted)]">
        <span>{segments.length} segments</span>
        {filteredCount > 0 && (
          <Tooltip label="Scored below the relevance threshold and withheld from generation. Still here — nothing is deleted.">
            <span className="flex cursor-help items-center gap-1">
              <EyeOff className="h-3 w-3" />
              {filteredCount} filtered
            </span>
          </Tooltip>
        )}
        <div className="flex-1" />
        {filteredCount > 0 && (
          <label className="flex cursor-pointer items-center gap-1.5">
            <Switch checked={hideFiltered} onCheckedChange={setHideFiltered} />
            Hide filtered
          </label>
        )}
        {timed && (
          <label className="flex cursor-pointer items-center gap-1.5">
            <Switch checked={followPlayback} onCheckedChange={setFollowPlayback} />
            Follow
          </label>
        )}
      </div>

      <div ref={listRef} className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2">
        {visible.map((segment) => {
          const active = segment.id === activeId
          const isHighlighted = highlighted.has(segment.id)
          return (
            <button
              key={segment.id}
              ref={active ? activeRef : undefined}
              data-segment={segment.id}
              data-active={active}
              data-low-relevance={segment.relevance < 0.3}
              onClick={() => {
                if (segment.start_ms !== null) seekTo(segment.start_ms)
                onSegmentClick?.(segment)
              }}
              className={cn(
                'transcript-line flex w-full gap-2.5 text-left',
                isHighlighted && 'ring-1 ring-[var(--accent)] ring-offset-1 ring-offset-[var(--bg)]',
              )}
            >
              <span className="w-11 shrink-0 pt-0.5 text-right font-mono text-[10px] text-[var(--text-muted)]">
                {segment.timestamp || (segment.page_no ? `p.${segment.page_no}` : '')}
              </span>
              <span className="min-w-0 flex-1">
                {segment.speaker && (
                  <span className="mr-1.5 text-[11px] font-semibold text-[var(--accent)]">
                    {segment.speaker}
                  </span>
                )}
                <span className="text-[13px] leading-relaxed">{segment.text}</span>
                {segment.is_marked && (
                  <Badge tone="warning" className="ml-1.5 align-middle">
                    <Star className="h-2.5 w-2.5" /> marked
                  </Badge>
                )}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
