import { useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen, Brain, FileText, Headphones, Layers, MessageSquare, Mic, Search,
  Settings as SettingsIcon, Sparkles, ListChecks, LayoutDashboard,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useUi } from '../lib/store'
import { cn } from '../lib/utils'
import { Dialog, DialogContent, Kbd, Spinner, Switch } from './ui'

const ACTIONS = [
  { label: 'Dashboard', to: '/', icon: LayoutDashboard },
  { label: 'Library', to: '/library', icon: BookOpen },
  { label: 'Review flashcards', to: '/review', icon: Brain },
  { label: 'Start a quiz', to: '/quiz', icon: ListChecks },
  { label: 'Chat with your notes', to: '/chat', icon: MessageSquare },
  { label: 'Podcasts', to: '/podcasts', icon: Headphones },
  { label: 'Record a lecture', to: '/record', icon: Mic },
  { label: 'Settings', to: '/settings', icon: SettingsIcon },
]

const KIND_ICON: Record<string, typeof FileText> = {
  note: FileText,
  segment: Layers,
  card: Brain,
  concept: Sparkles,
  source: BookOpen,
}

/** Global search and navigation (Cmd+K). */
export function CommandPalette() {
  const { paletteOpen, setPaletteOpen } = useUi()
  const [query, setQuery] = useState('')
  const [semantic, setSemantic] = useState(false)
  const [cursor, setCursor] = useState(0)
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (paletteOpen) {
      setQuery('')
      setCursor(0)
      // Radix moves focus on open; wait a frame so the input actually keeps it.
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [paletteOpen])

  const trimmed = query.trim()
  const { data, isFetching } = useQuery({
    queryKey: ['search', trimmed, semantic],
    queryFn: () => api.search(trimmed, semantic),
    enabled: trimmed.length >= 2,
    // Debounce-ish: keeps previous results visible while the next query is in flight, so the
    // list does not flicker empty on every keystroke.
    placeholderData: (previous) => previous,
    staleTime: 20_000,
  })

  const filteredActions = useMemo(() => {
    if (!trimmed) return ACTIONS
    const lowered = trimmed.toLowerCase()
    return ACTIONS.filter((action) => action.label.toLowerCase().includes(lowered))
  }, [trimmed])

  const results = data?.results ?? []
  const total = filteredActions.length + results.length

  useEffect(() => {
    setCursor(0)
  }, [trimmed, semantic])

  const go = (index: number) => {
    if (index < filteredActions.length) {
      const action = filteredActions[index]
      if (!action) return
      navigate({ to: action.to })
      setPaletteOpen(false)
      return
    }
    const hit = results[index - filteredActions.length]
    if (!hit) return
    setPaletteOpen(false)
    if (hit.kind === 'card') {
      navigate({ to: '/review' })
    } else if (hit.source_id) {
      // Segments and note blocks both resolve to the source page; the hash tells it where
      // to scroll and which tab to open.
      const tab = hit.kind === 'segment' ? 'transcript' : 'notes'
      navigate({ to: '/library/$sourceId', params: { sourceId: String(hit.source_id) }, search: { tab, seg: hit.kind === 'segment' ? hit.id : undefined } as never })
    }
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setCursor((c) => Math.min(total - 1, c + 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setCursor((c) => Math.max(0, c - 1))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      go(cursor)
    }
  }

  return (
    <Dialog open={paletteOpen} onOpenChange={setPaletteOpen}>
      <DialogContent hideClose className="top-[15%] max-w-xl translate-y-0 p-0">
        <div className="flex items-center gap-2 border-b border-[var(--border)] px-3.5 py-2.5">
          <Search className="h-4 w-4 shrink-0 text-[var(--text-muted)]" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search notes, transcripts, cards…"
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--text-muted)]"
          />
          {isFetching && <Spinner className="h-3.5 w-3.5 text-[var(--text-muted)]" />}
          <Kbd>esc</Kbd>
        </div>

        <div className="max-h-[55vh] overflow-y-auto p-1.5">
          {filteredActions.length > 0 && (
            <Section label="Go to">
              {filteredActions.map((action, index) => (
                <Row
                  key={action.to}
                  active={cursor === index}
                  onSelect={() => go(index)}
                  onHover={() => setCursor(index)}
                  icon={<action.icon className="h-4 w-4" />}
                  title={action.label}
                />
              ))}
            </Section>
          )}

          {trimmed.length >= 2 && (
            <Section label={`Results${results.length ? ` (${results.length})` : ''}`}>
              {results.length === 0 && !isFetching && (
                <p className="px-2.5 py-3 text-sm text-[var(--text-muted)]">
                  Nothing matched “{trimmed}”.
                  {!semantic && ' Try turning on semantic search below.'}
                </p>
              )}
              {results.map((hit, index) => {
                const Icon = KIND_ICON[hit.kind] ?? FileText
                const position = filteredActions.length + index
                return (
                  <Row
                    key={`${hit.kind}-${hit.id}`}
                    active={cursor === position}
                    onSelect={() => go(position)}
                    onHover={() => setCursor(position)}
                    icon={<Icon className="h-4 w-4" />}
                    title={hit.title || hit.kind}
                    subtitle={hit.snippet}
                    meta={hit.match === 'semantic' ? 'semantic' : hit.kind}
                  />
                )
              })}
            </Section>
          )}
        </div>

        <div className="flex items-center gap-2 border-t border-[var(--border)] px-3.5 py-2 text-xs text-[var(--text-muted)]">
          <Switch checked={semantic} onCheckedChange={setSemantic} id="semantic" />
          <label htmlFor="semantic" className="cursor-pointer select-none">
            Semantic search
          </label>
          <div className="flex-1" />
          <Kbd>↑↓</Kbd> navigate <Kbd>↵</Kbd> open
        </div>
      </DialogContent>
    </Dialog>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-1">
      <p className="px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        {label}
      </p>
      {children}
    </div>
  )
}

function Row({
  active, onSelect, onHover, icon, title, subtitle, meta,
}: {
  active: boolean
  onSelect: () => void
  onHover: () => void
  icon: React.ReactNode
  title: string
  subtitle?: string
  meta?: string
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      onMouseEnter={onHover}
      className={cn(
        'flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors',
        active ? 'bg-[var(--accent-soft)]' : 'hover:bg-[var(--bg-sunken)]',
      )}
    >
      <span className={cn('mt-0.5 shrink-0', active ? 'text-[var(--accent)]' : 'text-[var(--text-muted)]')}>
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{title}</span>
        {subtitle && (
          <span
            className="mt-0.5 block truncate text-xs text-[var(--text-muted)] [&_mark]:bg-[var(--accent-soft)] [&_mark]:text-[var(--accent)] [&_mark]:rounded-sm"
            dangerouslySetInnerHTML={{ __html: sanitiseSnippet(subtitle) }}
          />
        )}
      </span>
      {meta && <span className="shrink-0 text-[10px] text-[var(--text-muted)]">{meta}</span>}
    </button>
  )
}

/**
 * FTS5's `snippet()` returns text wrapped in `<mark>`, which we want to render. Everything
 * else is escaped first — the snippet contains the user's own documents, and an HTML tag
 * inside a PDF would otherwise be injected straight into the DOM.
 */
function sanitiseSnippet(raw: string): string {
  const escaped = raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return escaped.replace(/&lt;mark&gt;/g, '<mark>').replace(/&lt;\/mark&gt;/g, '</mark>')
}
