import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import {
  AlertTriangle, Brain, Download, FileText, Headphones, Layers, ListChecks, MessageSquare,
  Pencil, Printer, RefreshCw, Sparkles, Trash2,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import { useJobs, usePlayer } from '../lib/store'
import type { NoteStyle } from '../lib/types'
import { cn, formatDuration, kindLabel } from '../lib/utils'
import { CardText } from '../components/CardText'
import { NoteEditor } from '../components/editor/NoteEditor'
import { AudioPlayer } from '../components/player/AudioPlayer'
import { VideoPlayer } from '../components/player/VideoPlayer'
import { TranscriptPanel } from '../components/player/TranscriptPanel'
import {
  Badge, Button, Card, CardBody, Dialog, DialogContent, DialogDescription, DialogTitle,
  EmptyState, Field, Input, Progress, Select, SelectContent, SelectItem, SelectTrigger,
  SelectValue, Skeleton, Tabs, TabsContent, TabsList, TabsTrigger, Tooltip,
} from '../components/ui'
import { StatusBadge } from './Dashboard'

const STYLES: { value: NoteStyle; label: string; hint: string }[] = [
  { value: 'detailed', label: 'Detailed', hint: 'Prose with worked examples' },
  { value: 'concise', label: 'Concise', hint: 'Bullet-dominant, minimal prose' },
  { value: 'cornell', label: 'Cornell', hint: 'Cue column, notes, summary' },
  { value: 'exam_prep', label: 'Exam prep', hint: 'Definitions and likely-tested content first' },
]

export function SourcePage() {
  const { sourceId } = useParams({ from: '/library/$sourceId' })
  const search = useSearch({ from: '/library/$sourceId' })
  const id = Number(sourceId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [tab, setTab] = useState(search.tab ?? 'notes')
  const [highlightIds, setHighlightIds] = useState<number[]>([])
  const [scrollTo, setScrollTo] = useState<number | null>(search.seg ?? null)
  const [regenOpen, setRegenOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')

  const setPlayerSource = usePlayer((state) => state.setSource)
  const jobs = useJobs((state) => state.jobs)
  const lastSection = useJobs((state) => state.lastSection)

  const activeJob = useMemo(
    () =>
      Object.values(jobs).find(
        (job) =>
          job.source_id === id &&
          job.parent_id == null &&
          (job.status === 'running' || job.status === 'queued'),
      ),
    [jobs, id],
  )

  const { data, isLoading } = useQuery({
    queryKey: ['source', id],
    queryFn: () => api.getSource(id),
    refetchInterval: activeJob ? 2000 : false,
  })

  const { data: segmentData } = useQuery({
    queryKey: ['segments', id],
    queryFn: () => api.getSegments(id),
    enabled: !!data && data.counts.segments > 0,
  })

  useEffect(() => {
    setPlayerSource(id)
    return () => setPlayerSource(null)
  }, [id, setPlayerSource])

  useEffect(() => {
    if (search.tab) setTab(search.tab)
  }, [search.tab])

  const regenerate = useMutation({
    mutationFn: (body: { target: 'notes' | 'concepts' | 'cards' | 'quiz' | 'all'; style?: string }) =>
      api.regenerate(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries()
      setRegenOpen(false)
      toast.success('Regenerating — watch the progress in the sidebar.')
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed.'),
  })

  const rename = useMutation({
    mutationFn: () => api.updateSource(id, { title: titleDraft.trim() }),
    onSuccess: () => {
      queryClient.invalidateQueries()
      setRenaming(false)
    },
  })

  const remove = useMutation({
    mutationFn: () => api.deleteSource(id),
    onSuccess: () => {
      queryClient.invalidateQueries()
      toast.success('Deleted.')
      navigate({ to: '/library' })
    },
  })

  if (isLoading || !data) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-6">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-96" />
      </div>
    )
  }

  const { source, note, counts } = data
  const segments = segmentData?.segments ?? []
  const isMedia = source.kind === 'audio' || source.kind === 'video' || source.kind === 'youtube'
  const printMode = search.print === true

  const jumpToSource = (ids: number[]) => {
    setHighlightIds(ids)
    setScrollTo(ids[0] ?? null)
    setTab('transcript')
    const target = segments.find((segment) => segment.id === ids[0])
    if (target?.start_ms != null) usePlayer.getState().seekTo(target.start_ms)
  }

  if (printMode && note) {
    // A clean print view: the browser's own print-to-PDF handles the export, so no headless
    // Chromium has to ship in the container for one output format.
    return (
      <div className="mx-auto max-w-3xl p-8">
        <h1 className="mb-6 text-3xl font-semibold">{note.title}</h1>
        <NoteEditor noteId={note.id} doc={note.doc} editable={false} />
        <div className="no-print mt-8 flex gap-2 border-t border-[var(--border)] pt-4">
          <Button variant="primary" onClick={() => window.print()}>
            <Printer className="h-4 w-4" /> Print / Save as PDF
          </Button>
          <Button onClick={() => navigate({ to: '/library/$sourceId', params: { sourceId }, search: {} })}>
            Back
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-7xl p-4 sm:p-6">
      <header className="mb-4">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            {renaming ? (
              <div className="flex gap-2">
                <Input
                  autoFocus
                  value={titleDraft}
                  onChange={(event) => setTitleDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') rename.mutate()
                    if (event.key === 'Escape') setRenaming(false)
                  }}
                  className="max-w-md text-lg"
                />
                <Button variant="primary" onClick={() => rename.mutate()} loading={rename.isPending}>
                  Save
                </Button>
                <Button onClick={() => setRenaming(false)}>Cancel</Button>
              </div>
            ) : (
              <h1 className="group flex items-center gap-2 text-2xl font-semibold tracking-tight">
                <span className="min-w-0 break-words">{source.title}</span>
                <button
                  onClick={() => {
                    setTitleDraft(source.title)
                    setRenaming(true)
                  }}
                  className="shrink-0 rounded p-1 text-[var(--text-muted)] opacity-0 transition hover:bg-[var(--bg-sunken)] group-hover:opacity-100"
                  aria-label="Rename"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
              </h1>
            )}
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
              <StatusBadge status={source.status} />
              <span>{kindLabel(source.kind)}</span>
              {source.duration_sec ? <span>· {formatDuration(source.duration_sec)}</span> : null}
              {source.page_count ? <span>· {source.page_count} pages</span> : null}
              <span>· {counts.segments} segments</span>
              {counts.concepts > 0 && <span>· {counts.concepts} concepts</span>}
              {counts.cards > 0 && <span>· {counts.cards} cards</span>}
            </div>
          </div>

          <div className="flex flex-wrap gap-1.5">
            <Tooltip label="Chat with this source">
              <Button
                size="sm"
                onClick={() => navigate({ to: '/chat', search: { source: id } })}
              >
                <MessageSquare className="h-3.5 w-3.5" />
              </Button>
            </Tooltip>
            <Tooltip label="Quiz on this source">
              <Button size="sm" onClick={() => navigate({ to: '/quiz', search: { source: id } })}>
                <ListChecks className="h-3.5 w-3.5" />
              </Button>
            </Tooltip>
            <Tooltip label="Review its flashcards">
              <Button
                size="sm"
                disabled={counts.cards === 0}
                onClick={() => navigate({ to: '/review', search: { source: id } })}
              >
                <Brain className="h-3.5 w-3.5" />
              </Button>
            </Tooltip>
            <Button size="sm" onClick={() => setRegenOpen(true)}>
              <RefreshCw className="h-3.5 w-3.5" /> Regenerate
            </Button>
            {note && <ExportMenu noteId={note.id} sourceId={id} />}
            <Tooltip label="Delete this source">
              <Button
                size="sm"
                variant="ghost"
                className="text-red-500"
                onClick={() => {
                  if (confirm(`Delete “${source.title}” and everything generated from it?`)) {
                    remove.mutate()
                  }
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </Tooltip>
          </div>
        </div>

        {activeJob && (
          <Card className="mt-3 border-[var(--accent)]/40">
            <CardBody className="py-2.5">
              <div className="mb-1.5 flex items-center justify-between text-xs">
                <span className="font-medium">{activeJob.stage || 'Processing…'}</span>
                <span className="tabular-nums text-[var(--text-muted)]">{activeJob.progress}%</span>
              </div>
              <Progress value={activeJob.progress} />
              {lastSection?.sourceId === id && lastSection.total > 0 && (
                <p className="mt-1.5 text-[11px] text-[var(--text-muted)]">
                  Section {lastSection.done} of {lastSection.total}: {lastSection.section}
                </p>
              )}
            </CardBody>
          </Card>
        )}

        {source.status === 'failed' && source.error && (
          <Card className="mt-3 border-red-500/40 bg-red-500/5">
            <CardBody className="flex items-start gap-2.5 py-2.5">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">Processing failed</p>
                <p className="mt-0.5 text-xs text-[var(--text-muted)]">{source.error}</p>
              </div>
              <Button size="sm" onClick={() => api.reprocess(id).then(() => queryClient.invalidateQueries())}>
                Retry
              </Button>
            </CardBody>
          </Card>
        )}

        {source.status === 'ready' && source.error && (
          <Card className="mt-3 border-amber-500/40 bg-amber-500/5">
            <CardBody className="flex items-start gap-2.5 py-2.5">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">Finished with some steps incomplete</p>
                <p className="mt-0.5 text-xs text-[var(--text-muted)]">{source.error}</p>
              </div>
            </CardBody>
          </Card>
        )}
      </header>

      {isMedia && source.has_media && (
        <Card className="mb-4">
          <CardBody className="py-3">
            {source.kind === 'video' ? (
              <VideoPlayer src={api.mediaUrl(id)} audioSrc={api.audioUrl(id)} />
            ) : (
              <AudioPlayer src={api.audioUrl(id)} />
            )}
          </CardBody>
        </Card>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-3">
          <TabsTrigger value="notes">
            <FileText className="h-3.5 w-3.5" /> Notes
          </TabsTrigger>
          <TabsTrigger value="transcript">
            <Layers className="h-3.5 w-3.5" /> {isMedia ? 'Transcript' : 'Source text'}
            <Badge className="ml-1">{counts.segments}</Badge>
          </TabsTrigger>
          <TabsTrigger value="concepts">
            <Sparkles className="h-3.5 w-3.5" /> Concepts
            <Badge className="ml-1">{counts.concepts}</Badge>
          </TabsTrigger>
          <TabsTrigger value="cards">
            <Brain className="h-3.5 w-3.5" /> Cards
            <Badge className="ml-1">{counts.cards}</Badge>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="notes">
          {note ? (
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
              <Card>
                <CardBody className="px-5 py-4 sm:px-8 sm:py-6">
                  <NoteEditor
                    noteId={note.id}
                    doc={note.doc}
                    onAnchorClick={jumpToSource}
                    onDirty={() => queryClient.setQueryData(['source', id], (old: typeof data) =>
                      old?.note ? { ...old, note: { ...old.note, is_dirty: true } } : old,
                    )}
                  />
                </CardBody>
              </Card>

              {note.outline.length > 0 && (
                <aside className="hidden lg:block">
                  <div className="sticky top-4">
                    <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                      On this page
                    </p>
                    <nav className="space-y-0.5">
                      {note.outline.map((item, index) => (
                        <a
                          key={index}
                          href={`#heading-${item.index}`}
                          onClick={(event) => {
                            event.preventDefault()
                            const headings = document.querySelectorAll('.ProseMirror h1, .ProseMirror h2, .ProseMirror h3, .ProseMirror h4')
                            const seen = Array.from(headings).find((h) => h.textContent === item.text)
                            seen?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                          }}
                          className={cn(
                            'block truncate rounded px-2 py-1 text-[13px] text-[var(--text-muted)] hover:bg-[var(--bg-sunken)] hover:text-[var(--text)]',
                            item.level >= 3 && 'pl-5 text-xs',
                          )}
                        >
                          {item.text}
                        </a>
                      ))}
                    </nav>
                  </div>
                </aside>
              )}
            </div>
          ) : (
            <Card>
              <EmptyState
                icon={FileText}
                title={activeJob ? 'Notes are being written…' : 'No notes yet'}
                description={
                  activeJob
                    ? 'Sections appear as they finish.'
                    : 'Generate notes from this source, or check that a language model is configured in Settings.'
                }
                action={
                  !activeJob && (
                    <Button variant="primary" onClick={() => regenerate.mutate({ target: 'notes' })}>
                      Generate notes
                    </Button>
                  )
                }
              />
            </Card>
          )}
        </TabsContent>

        <TabsContent value="transcript">
          <Card className="h-[70vh]">
            <TranscriptPanel
              segments={segments}
              highlightIds={highlightIds}
              scrollToSegment={scrollTo}
            />
          </Card>
        </TabsContent>

        <TabsContent value="concepts">
          <ConceptsTab sourceId={id} onJump={jumpToSource} />
        </TabsContent>

        <TabsContent value="cards">
          <CardsTab sourceId={id} count={counts.cards} onGenerate={() => regenerate.mutate({ target: 'cards' })} />
        </TabsContent>
      </Tabs>

      <RegenerateDialog
        open={regenOpen}
        onOpenChange={setRegenOpen}
        noteWasEdited={note?.is_dirty ?? false}
        pending={regenerate.isPending}
        onConfirm={(target, style) => regenerate.mutate({ target, style })}
      />
    </div>
  )
}

function ExportMenu({ noteId, sourceId }: { noteId: number; sourceId: number }) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const formats = [
    { id: 'md', label: 'Markdown' },
    { id: 'docx', label: 'Word (.docx)' },
    { id: 'html', label: 'HTML' },
    { id: 'txt', label: 'Plain text' },
  ]
  return (
    <div className="relative">
      <Button size="sm" onClick={() => setOpen(!open)}>
        <Download className="h-3.5 w-3.5" /> Export
      </Button>
      {open && (
        <>
          <button className="fixed inset-0 z-10" onClick={() => setOpen(false)} aria-hidden />
          <div className="absolute right-0 top-9 z-20 w-44 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] py-1 shadow-xl">
            {formats.map((format) => (
              <a
                key={format.id}
                href={api.exportUrl(noteId, format.id)}
                onClick={() => setOpen(false)}
                className="block px-3 py-1.5 text-sm hover:bg-[var(--bg-sunken)]"
              >
                {format.label}
              </a>
            ))}
            <button
              onClick={() => {
                setOpen(false)
                navigate({
                  to: '/library/$sourceId',
                  params: { sourceId: String(sourceId) },
                  search: { print: true },
                })
              }}
              className="flex w-full items-center gap-2 border-t border-[var(--border)] px-3 py-1.5 text-left text-sm hover:bg-[var(--bg-sunken)]"
            >
              <Printer className="h-3.5 w-3.5" /> Print / PDF
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function RegenerateDialog({
  open, onOpenChange, noteWasEdited, pending, onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  noteWasEdited: boolean
  pending: boolean
  onConfirm: (target: 'notes' | 'concepts' | 'cards' | 'quiz' | 'all', style: string) => void
}) {
  const [style, setStyle] = useState<string>('detailed')
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Regenerate</DialogTitle>
        <DialogDescription>
          Re-run part of the pipeline with the current provider and settings.
        </DialogDescription>

        {noteWasEdited && (
          <div className="mb-4 flex items-start gap-2.5 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
            <p className="text-xs">
              You have edited these notes by hand. Regenerating replaces the document and your
              edits will be lost.
            </p>
          </div>
        )}

        <Field label="Note style" hint={STYLES.find((s) => s.value === style)?.hint}>
          <Select value={style} onValueChange={setStyle}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STYLES.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        <div className="mt-5 grid gap-2 sm:grid-cols-2">
          <Button variant="primary" loading={pending} onClick={() => onConfirm('notes', style)}>
            <FileText className="h-4 w-4" /> Notes only
          </Button>
          <Button onClick={() => onConfirm('concepts', style)} disabled={pending}>
            <Sparkles className="h-4 w-4" /> Concepts
          </Button>
          <Button onClick={() => onConfirm('cards', style)} disabled={pending}>
            <Brain className="h-4 w-4" /> Flashcards
          </Button>
          <Button onClick={() => onConfirm('quiz', style)} disabled={pending}>
            <ListChecks className="h-4 w-4" /> Quiz questions
          </Button>
          <Button className="sm:col-span-2" onClick={() => onConfirm('all', style)} disabled={pending}>
            <RefreshCw className="h-4 w-4" /> Everything
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function ConceptsTab({ sourceId, onJump }: { sourceId: number; onJump: (ids: number[]) => void }) {
  const { data } = useQuery({
    queryKey: ['concepts', sourceId],
    queryFn: () => api.getConcepts(sourceId),
  })
  const concepts = data?.concepts ?? []

  if (concepts.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={Sparkles}
          title="No concepts extracted yet"
          description="Concepts are the units flashcards and quiz questions are built from. Use Regenerate → Concepts."
        />
      </Card>
    )
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {concepts.map((concept) => (
        <Card key={concept.id}>
          <CardBody>
            <div className="flex items-start justify-between gap-2">
              <h3 className="font-medium">{concept.name}</h3>
              <Badge tone={concept.importance >= 4 ? 'accent' : 'neutral'}>
                {'★'.repeat(concept.importance)}
              </Badge>
            </div>
            <p className="mt-1 text-sm text-[var(--text-muted)]">{concept.definition}</p>
            <div className="mt-3 flex items-center gap-2">
              <Progress
                value={concept.mastery * 100}
                tone={concept.mastery >= 0.8 ? 'success' : concept.mastery < 0.35 ? 'danger' : 'accent'}
                className="flex-1"
              />
              <span className="tabular-nums text-[11px] text-[var(--text-muted)]">
                {Math.round(concept.mastery * 100)}%
              </span>
              {concept.segment_ids.length > 0 && (
                <Button variant="ghost" size="icon-sm" onClick={() => onJump(concept.segment_ids)}>
                  <Layers className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
          </CardBody>
        </Card>
      ))}
    </div>
  )
}

function CardsTab({
  sourceId, count, onGenerate,
}: {
  sourceId: number
  count: number
  onGenerate: () => void
}) {
  const { data } = useQuery({
    queryKey: ['cards', sourceId],
    queryFn: () => api.listCards(sourceId),
    enabled: count > 0,
  })
  const navigate = useNavigate()

  if (count === 0) {
    return (
      <Card>
        <EmptyState
          icon={Brain}
          title="No flashcards yet"
          description="Cards are generated from this source's concepts and scheduled with FSRS."
          action={<Button variant="primary" onClick={onGenerate}>Generate flashcards</Button>}
        />
      </Card>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => navigate({ to: '/review', search: { source: sourceId } })}>
          <Brain className="h-4 w-4" /> Review these
        </Button>
        <Button
          onClick={async () => {
            try {
              const { blob } = await api.exportAnki({ source_ids: [sourceId] })
              const { downloadBlob } = await import('../lib/utils')
              downloadBlob(blob, 'quicklearn.apkg')
              toast.success('Exported to Anki.')
            } catch (error) {
              toast.error(error instanceof ApiError ? error.message : 'Export failed.')
            }
          }}
        >
          <Download className="h-4 w-4" /> Export to Anki
        </Button>
        <Button onClick={() => navigate({ to: '/podcasts' })}>
          <Headphones className="h-4 w-4" /> Make a podcast
        </Button>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {(data?.cards ?? []).map((card) => (
          <Card key={card.id}>
            <CardBody className="py-3">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium">
                  <CardText text={card.front} revealed />
                </p>
                <Badge>{card.type}</Badge>
              </div>
              <p className="mt-1.5 border-t border-[var(--border)] pt-1.5 text-sm text-[var(--text-muted)]">
                <CardText text={card.back} revealed />
              </p>
              <p className="mt-2 text-[10px] text-[var(--text-muted)]">
                {card.state} · {card.reps} review{card.reps === 1 ? '' : 's'}
                {card.lapses > 0 && ` · ${card.lapses} lapse${card.lapses === 1 ? '' : 's'}`}
              </p>
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  )
}
