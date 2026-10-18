import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearch } from '@tanstack/react-router'
import { Brain, Check, Download, PartyPopper, Undo2, Upload } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import type { Card as CardType } from '../lib/types'
import { cn, downloadBlob } from '../lib/utils'
import { ClozeText, clozeAnswers } from '../components/ClozeText'
import {
  Badge, Button, Card, CardBody, EmptyState, Kbd, Progress, Skeleton,
} from '../components/ui'

const RATINGS = [
  { value: 1, label: 'Again', key: '1', tone: 'bg-red-500 hover:bg-red-600' },
  { value: 2, label: 'Hard', key: '2', tone: 'bg-amber-500 hover:bg-amber-600' },
  { value: 3, label: 'Good', key: '3', tone: 'bg-emerald-500 hover:bg-emerald-600' },
  { value: 4, label: 'Easy', key: '4', tone: 'bg-sky-500 hover:bg-sky-600' },
] as const

export function ReviewPage() {
  const search = useSearch({ from: '/review' })
  const queryClient = useQueryClient()

  const [index, setIndex] = useState(0)
  const [revealed, setRevealed] = useState(false)
  const [answered, setAnswered] = useState(0)
  const [lastCardId, setLastCardId] = useState<number | null>(null)
  const shownAt = useRef<number>(Date.now())
  const touchStart = useRef<{ x: number; y: number } | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['due-cards', search.folder, search.source],
    queryFn: () => api.dueCards({ folder_id: search.folder, source_id: search.source, limit: 60 }),
  })

  const cards = data?.cards ?? []
  const card: CardType | undefined = cards[index]

  useEffect(() => {
    shownAt.current = Date.now()
  }, [index])

  const rate = useMutation({
    mutationFn: ({ id, rating }: { id: number; rating: number }) =>
      api.reviewCard(id, rating, Date.now() - shownAt.current),
    onSuccess: (result) => {
      setLastCardId(result.card_id)
      setAnswered((count) => count + 1)
      setRevealed(false)
      setIndex((current) => current + 1)
      queryClient.invalidateQueries({ queryKey: ['card-counts'] })
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed to save.'),
  })

  const undo = useMutation({
    mutationFn: (id: number) => api.undoReview(id),
    onSuccess: () => {
      setIndex((current) => Math.max(0, current - 1))
      setAnswered((count) => Math.max(0, count - 1))
      setRevealed(false)
      setLastCardId(null)
      queryClient.invalidateQueries({ queryKey: ['card-counts'] })
      toast.success('Undone.')
    },
  })

  const submit = useCallback(
    (rating: number) => {
      if (!card || rate.isPending) return
      rate.mutate({ id: card.id, rating })
    },
    [card, rate],
  )

  // Keyboard-driven review (PRD §7.3): space reveals, 1–4 rate. This is the interaction that
  // determines whether reviewing 60 cards is pleasant or a chore.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return
      if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault()
        if (!revealed) setRevealed(true)
        else submit(3)
      } else if (revealed && ['1', '2', '3', '4'].includes(event.key)) {
        event.preventDefault()
        submit(Number(event.key))
      } else if (event.key === 'z' && (event.metaKey || event.ctrlKey) && lastCardId) {
        event.preventDefault()
        undo.mutate(lastCardId)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [revealed, submit, lastCardId, undo])

  if (isLoading) {
    return (
      <div className="mx-auto max-w-2xl space-y-4 p-6">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-72" />
      </div>
    )
  }

  const counts = data?.counts
  const finished = index >= cards.length

  if (cards.length === 0) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <Card>
          <EmptyState
            icon={Brain}
            title={counts?.total ? 'Nothing due right now' : 'No flashcards yet'}
            description={
              counts?.total
                ? `You have ${counts.total} card${counts.total === 1 ? '' : 's'} scheduled. Come back when they are due — that is how spaced repetition works.`
                : 'Process a source to generate flashcards, or import an existing Anki deck.'
            }
            action={
              <div className="flex gap-2">
                <Link to="/library">
                  <Button variant="primary">Go to library</Button>
                </Link>
                <ImportButton />
              </div>
            }
          />
        </Card>
        {(counts?.total ?? 0) > 0 && <Forecast />}
      </div>
    )
  }

  if (finished) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <Card>
          <EmptyState
            icon={PartyPopper}
            title={`${answered} card${answered === 1 ? '' : 's'} reviewed`}
            description="That's everything due in this batch."
            action={
              <div className="flex gap-2">
                <Button
                  variant="primary"
                  onClick={() => {
                    setIndex(0)
                    setAnswered(0)
                    void refetch()
                  }}
                >
                  Check for more
                </Button>
                <Link to="/">
                  <Button>Dashboard</Button>
                </Link>
              </div>
            }
          />
        </Card>
        <Forecast />
      </div>
    )
  }

  if (!card) return null

  return (
    <div className="mx-auto flex min-h-full max-w-2xl flex-col p-4 sm:p-6">
      <div className="mb-3 flex items-center gap-3 text-xs text-[var(--text-muted)]">
        <span className="tabular-nums">
          {index + 1} / {cards.length}
        </span>
        <Progress value={((index + 1) / cards.length) * 100} className="flex-1" />
        {counts && (
          <span className="hidden sm:inline">
            {counts.due} due · {counts.new} new
          </span>
        )}
        {lastCardId && (
          <Button variant="ghost" size="icon-sm" onClick={() => undo.mutate(lastCardId)}>
            <Undo2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>

      <Card
        className="flex min-h-[22rem] flex-1 flex-col"
        onTouchStart={(event) => {
          const touch = event.touches[0]
          if (touch) touchStart.current = { x: touch.clientX, y: touch.clientY }
        }}
        onTouchEnd={(event) => {
          // Mobile swipe ratings (PRD §7.3). Horizontal only, so vertical scrolling still works.
          const start = touchStart.current
          const touch = event.changedTouches[0]
          if (!start || !touch || !revealed) return
          const dx = touch.clientX - start.x
          const dy = touch.clientY - start.y
          if (Math.abs(dx) > 80 && Math.abs(dx) > Math.abs(dy) * 1.5) {
            submit(dx > 0 ? 3 : 1)
          }
          touchStart.current = null
        }}
      >
        <CardBody className="flex flex-1 flex-col">
          <div className="mb-2 flex items-center gap-2">
            <Badge tone={card.state === 'new' ? 'accent' : 'neutral'}>{card.state}</Badge>
            <Badge>{card.type}</Badge>
            {card.source_title && (
              <span className="truncate text-[11px] text-[var(--text-muted)]">
                {card.source_title}
              </span>
            )}
          </div>

          <div className="flex flex-1 flex-col justify-center py-6">
            <p className="whitespace-pre-wrap text-center text-lg leading-relaxed">
              <ClozeText text={card.front} revealed={revealed} />
            </p>

            {/* A cloze card's back usually just repeats the answer the front now shows. */}
            {revealed && card.back.trim() !== clozeAnswers(card.front).join(', ') && (
              <div className="animate-in-up mt-6 border-t border-[var(--border)] pt-6">
                <p className="whitespace-pre-wrap text-center text-[17px] leading-relaxed text-[var(--text-muted)]">
                  {card.back}
                </p>
              </div>
            )}
          </div>
        </CardBody>
      </Card>

      <div className="mt-4">
        {!revealed ? (
          <Button
            variant="primary"
            size="lg"
            className="w-full justify-center"
            onClick={() => setRevealed(true)}
          >
            Show answer <Kbd>space</Kbd>
          </Button>
        ) : (
          <div className="grid grid-cols-4 gap-2">
            {RATINGS.map((rating) => (
              <button
                key={rating.value}
                onClick={() => submit(rating.value)}
                disabled={rate.isPending}
                className={cn(
                  'flex flex-col items-center gap-0.5 rounded-lg px-2 py-2.5 text-white transition-colors disabled:opacity-60',
                  rating.tone,
                )}
              >
                <span className="text-sm font-medium">{rating.label}</span>
                <span className="text-[10px] tabular-nums opacity-90">
                  {card.intervals?.[String(rating.value)] ?? '—'}
                </span>
              </button>
            ))}
          </div>
        )}
        <p className="mt-2 text-center text-[11px] text-[var(--text-muted)]">
          <Kbd>space</Kbd> reveal · <Kbd>1</Kbd>–<Kbd>4</Kbd> rate · swipe on mobile
        </p>
      </div>
    </div>
  )
}

function Forecast() {
  const { data } = useQuery({ queryKey: ['forecast'], queryFn: () => api.forecast(21) })
  const { data: stats } = useQuery({ queryKey: ['card-stats'], queryFn: () => api.cardStats() })
  const forecast = data?.forecast ?? []
  const max = Math.max(1, ...forecast.map((day) => day.count))

  if (forecast.length === 0) return null

  return (
    <Card className="mt-4">
      <CardBody>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">Next three weeks</h3>
          {stats && (
            <span className="text-xs text-[var(--text-muted)]">
              {stats.total_reviews} reviews · {stats.streak_days}-day streak
            </span>
          )}
        </div>
        <div className="flex h-24 items-end gap-1">
          {forecast.map((day) => (
            <div key={day.date} className="group relative flex-1" title={`${day.count} on ${day.date}`}>
              <div
                className="w-full rounded-t bg-[var(--accent)] transition-opacity group-hover:opacity-80"
                style={{ height: `${Math.max(2, (day.count / max) * 92)}px` }}
              />
            </div>
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[10px] text-[var(--text-muted)]">
          <span>today</span>
          <span>+3 weeks</span>
        </div>

        {stats && stats.can_optimize && (
          <div className="mt-4 border-t border-[var(--border)] pt-3">
            <OptimizeButton available={stats.optimizer_available} />
          </div>
        )}
      </CardBody>
    </Card>
  )
}

function OptimizeButton({ available }: { available: boolean }) {
  const optimize = useMutation({
    mutationFn: () => api.optimizeFsrs(),
    onSuccess: (result) =>
      toast.success(`FSRS tuned to your history — ${result.count} parameters updated.`),
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Optimisation failed.', {
        duration: 10_000,
      }),
  })
  return (
    <div className="flex items-center gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium">Optimise FSRS for your memory</p>
        <p className="text-[11px] text-[var(--text-muted)]">
          {available
            ? 'You have enough review history to fit personal scheduling parameters.'
            : 'Needs the optional optimiser extra (PyTorch). Click for the install command.'}
        </p>
      </div>
      <Button size="sm" loading={optimize.isPending} onClick={() => optimize.mutate()}>
        Optimise
      </Button>
    </div>
  )
}

function ImportButton() {
  const queryClient = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)

  return (
    <>
      <Button
        loading={busy}
        onClick={() => input.current?.click()}
      >
        <Upload className="h-4 w-4" /> Import deck
      </Button>
      <input
        ref={input}
        type="file"
        accept=".apkg,.csv,.tsv,.txt"
        className="hidden"
        onChange={async (event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (!file) return
          setBusy(true)
          try {
            const result = await api.importCards(file)
            toast.success(
              `Imported ${result.imported} card${result.imported === 1 ? '' : 's'}${result.skipped ? ` (${result.skipped} skipped)` : ''}.`,
            )
            queryClient.invalidateQueries()
          } catch (error) {
            toast.error(error instanceof ApiError ? error.message : 'Import failed.')
          } finally {
            setBusy(false)
          }
        }}
      />
    </>
  )
}

export function ExportAnkiButton({ sourceIds }: { sourceIds?: number[] }) {
  const [busy, setBusy] = useState(false)
  return (
    <Button
      loading={busy}
      onClick={async () => {
        setBusy(true)
        try {
          const { blob, count } = await api.exportAnki({ source_ids: sourceIds })
          downloadBlob(blob, 'quicklearn.apkg')
          toast.success(`Exported ${count ?? ''} cards.`, {
            description: 'Open the file in Anki to import, scheduling included.',
          })
        } catch (error) {
          toast.error(error instanceof ApiError ? error.message : 'Export failed.')
        } finally {
          setBusy(false)
        }
      }}
    >
      <Download className="h-4 w-4" /> Export to Anki
      <Check className="hidden" />
    </Button>
  )
}
