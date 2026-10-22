import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import {
  ArrowRight, BookOpen, CheckCircle2, Circle, GraduationCap, Hammer, Layers, Lightbulb,
  MessageSquare, Pencil, Printer, RotateCcw, ScrollText, Target, XCircle,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../../lib/api'
import type { CheckResult, CourseDetail, Lesson, LessonAnswer, LessonCheck, WarmupItem } from '../../lib/types'
import { cn } from '../../lib/utils'
import { InlineMarkdown, Markdown } from '../Markdown'
import { Button, Card, CardBody, EmptyState, Progress, Skeleton, Textarea } from '../ui'

// Local patch: the Learn tab. A source as a short guided course, after the "teach" skill
// (github.com/mattpocock/skills): a mission, small lessons that each end in retrieval practice
// with immediate feedback, warm-ups that bring back earlier lessons, and a reference sheet.

/** Which lesson is open, or the reference sheet. Held by the Source page: switching tabs unmounts this one. */
export type LearnView = number | 'reference' | null

export function LearnTab({
  sourceId, view, onView, onJump, building, ready,
}: {
  sourceId: number
  view: LearnView
  onView: (view: LearnView) => void
  onJump: (ids: number[]) => void
  building: boolean
  ready: boolean
}) {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['course', sourceId],
    queryFn: () => api.getCourse(sourceId),
    refetchInterval: building ? 3000 : false,
  })

  // One last fetch when the job finishes, so the reference sheet and final lesson show up.
  const wasBuilding = useRef(building)
  useEffect(() => {
    if (wasBuilding.current && !building) queryClient.invalidateQueries({ queryKey: ['course', sourceId] })
    wasBuilding.current = building
  }, [building, queryClient, sourceId])

  if (!data) return <Skeleton className="h-96" />

  const written = data.lessons.filter((lesson) => lesson.body_md)
  if (!data.course || data.lessons.length === 0) {
    return <BuildCourse sourceId={sourceId} mission={data.course?.mission ?? ''} building={building} ready={ready} />
  }

  const selected =
    view === 'reference'
      ? 'reference'
      : (data.lessons.find((lesson) => lesson.id === view) ??
        data.lessons.find((lesson) => lesson.id === data.next_lesson_id) ??
        data.lessons[0])

  return (
    <div className="grid gap-4 lg:grid-cols-[17rem_minmax(0,1fr)]">
      <aside className="space-y-3 lg:sticky lg:top-4 lg:self-start">
        <MissionCard sourceId={sourceId} mission={data.course.mission} building={building} ready={ready} />
        <CourseOutline
          data={data}
          selected={selected === 'reference' ? 'reference' : selected.id}
          onView={onView}
        />
      </aside>

      <main className="min-w-0">
        {building && (
          <p className="mb-3 rounded-md bg-[var(--bg-sunken)] px-3 py-2 text-xs text-[var(--text-muted)]">
            Building the course: {written.length} of {data.lessons.length} lessons written. Each one appears here as it finishes.
          </p>
        )}
        {selected === 'reference' ? (
          <ReferenceSheet data={data} building={building} sourceId={sourceId} />
        ) : (
          <LessonView
            key={selected.id}
            sourceId={sourceId}
            lesson={selected}
            lessons={data.lessons}
            nextLessonId={data.next_lesson_id}
            building={building}
            onJump={onJump}
            onView={onView}
          />
        )}
      </main>
    </div>
  )
}

function useBuild(sourceId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (mission?: string) => api.buildCourse(sourceId, mission),
    onSuccess: () => {
      queryClient.invalidateQueries()
      toast.success('Building your course. Lessons appear as they are written.')
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed.'),
  })
}

function BuildCourse({
  sourceId, mission: savedMission, building, ready,
}: {
  sourceId: number
  mission: string
  building: boolean
  ready: boolean
}) {
  const [mission, setMission] = useState(savedMission)
  const build = useBuild(sourceId)

  if (building) {
    return (
      <Card>
        <EmptyState
          icon={GraduationCap}
          title="Planning your course…"
          description="Lessons appear here as they are written. On the local model this takes a few minutes."
        />
      </Card>
    )
  }

  return (
    <Card>
      <CardBody className="mx-auto max-w-xl space-y-4 py-8">
        <div className="text-center">
          <GraduationCap className="mx-auto mb-2 h-8 w-8 text-[var(--accent)]" />
          <h2 className="text-lg font-semibold">Learn this, step by step</h2>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Turns this source into a short course: small lessons that each teach one thing, check
            what you remember right away, bring back earlier lessons, and end with a reference sheet.
          </p>
        </div>
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">Why are you learning this?</span>
          <Textarea
            value={mission}
            onChange={(event) => setMission(event.target.value)}
            rows={3}
            placeholder="e.g. I have a data structures exam in two weeks, or I need to explain this at work"
          />
          <span className="block text-xs text-[var(--text-muted)]">
            Optional, but every lesson is pitched at this goal. You can change it later.
          </span>
        </label>
        <Button
          variant="primary"
          className="w-full justify-center"
          disabled={!ready}
          loading={build.isPending}
          onClick={() => build.mutate(mission)}
        >
          <GraduationCap className="h-4 w-4" /> {ready ? 'Build my course' : 'Available once processing finishes'}
        </Button>
      </CardBody>
    </Card>
  )
}

function MissionCard({
  sourceId, mission, building, ready,
}: {
  sourceId: number
  mission: string
  building: boolean
  ready: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(mission)
  const queryClient = useQueryClient()
  const build = useBuild(sourceId)
  const save = useMutation({
    mutationFn: () => api.saveMission(sourceId, draft),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['course', sourceId] })
      setEditing(false)
    },
  })

  return (
    <Card>
      <CardBody className="space-y-2 py-3">
        <div className="flex items-center justify-between">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">Your goal</p>
          {!editing && (
            <button
              onClick={() => {
                setDraft(mission)
                setEditing(true)
              }}
              className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--bg-sunken)]"
              aria-label="Edit your goal"
            >
              <Pencil className="h-3 w-3" />
            </button>
          )}
        </div>
        {editing ? (
          <>
            <Textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={3} autoFocus />
            <div className="flex flex-wrap gap-1.5">
              <Button size="sm" onClick={() => save.mutate()} loading={save.isPending}>Save</Button>
              <Button
                size="sm"
                variant="primary"
                disabled={building || !ready}
                onClick={() => {
                  if (confirm('Rebuild the course for this goal? Lessons are rewritten and your lesson progress starts over (concept mastery is kept).')) {
                    build.mutate(draft, { onSuccess: () => setEditing(false) })
                  }
                }}
              >
                Save & rebuild
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            </div>
          </>
        ) : (
          <p className={cn('text-sm', !mission && 'text-[var(--text-muted)]')}>
            {mission || 'Not set. Saying why you are learning this makes the lessons more specific.'}
          </p>
        )}
      </CardBody>
    </Card>
  )
}

function CourseOutline({
  data, selected, onView,
}: {
  data: CourseDetail
  selected: number | 'reference'
  onView: (view: LearnView) => void
}) {
  const finished = data.lessons.filter((lesson) => lesson.status === 'done' || lesson.status === 'review').length
  return (
    <Card>
      <CardBody className="space-y-2 py-3">
        <div>
          <div className="mb-1 flex justify-between text-[11px] text-[var(--text-muted)]">
            <span>{finished} of {data.lessons.length} lessons done</span>
          </div>
          <Progress value={(finished / Math.max(1, data.lessons.length)) * 100} tone="success" />
        </div>
        <nav className="-mx-1 space-y-0.5">
          {data.lessons.map((lesson) => (
            <button
              key={lesson.id}
              onClick={() => onView(lesson.id)}
              className={cn(
                'flex w-full items-start gap-2 rounded-md px-1.5 py-1.5 text-left text-[13px] transition-colors hover:bg-[var(--bg-sunken)]',
                selected === lesson.id && 'bg-[var(--bg-sunken)] font-medium',
              )}
            >
              <StatusIcon lesson={lesson} upNext={lesson.id === data.next_lesson_id} />
              <span className="min-w-0 flex-1">
                <span className={cn(lesson.status === 'missing' && 'text-[var(--text-muted)]')}>
                  {lesson.index + 1}. {lesson.title}
                </span>
                {lesson.id === data.next_lesson_id && (
                  <span className="ml-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--accent)]">
                    {lesson.status === 'review' ? 'review' : 'up next'}
                  </span>
                )}
              </span>
            </button>
          ))}
          <button
            onClick={() => onView('reference')}
            className={cn(
              'flex w-full items-center gap-2 rounded-md border-t border-[var(--border)] px-1.5 py-1.5 text-left text-[13px] hover:bg-[var(--bg-sunken)]',
              selected === 'reference' && 'bg-[var(--bg-sunken)] font-medium',
            )}
          >
            <ScrollText className="h-4 w-4 shrink-0 text-[var(--text-muted)]" /> Reference sheet
          </button>
        </nav>
      </CardBody>
    </Card>
  )
}

function StatusIcon({ lesson, upNext }: { lesson: Lesson; upNext: boolean }) {
  const className = 'mt-0.5 h-4 w-4 shrink-0'
  if (lesson.status === 'done') return <CheckCircle2 className={cn(className, 'text-emerald-500')} />
  if (lesson.status === 'review') return <RotateCcw className={cn(className, 'text-amber-500')} />
  if (lesson.status === 'missing') return <Circle className={cn(className, 'text-[var(--border)]')} />
  return <Circle className={cn(className, upNext ? 'text-[var(--accent)]' : 'text-[var(--text-muted)]')} />
}

function Section({ icon: Icon, title, children, className }: {
  icon: typeof BookOpen
  title: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <section className={cn('space-y-2', className)}>
      <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        <Icon className="h-3.5 w-3.5" /> {title}
      </h3>
      {children}
    </section>
  )
}

function LessonView({
  sourceId, lesson, lessons, nextLessonId, building, onJump, onView,
}: {
  sourceId: number
  lesson: Lesson
  lessons: Lesson[]
  nextLessonId: number | null
  building: boolean
  onJump: (ids: number[]) => void
  onView: (view: LearnView) => void
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const following = lessons.find((row) => row.index === lesson.index + 1)
  const cite = (id: number) => onJump([id])
  const answered = lesson.checks.filter((check) => check.result).length

  const answer = async (itemId: number, response: string) => {
    const result = await api.answerLessonCheck(lesson.id, itemId, response)
    queryClient.invalidateQueries({ queryKey: ['course', sourceId] })
    if (result.lesson_completed) toast.success(`Lesson ${lesson.index + 1} complete.`)
    return result
  }

  if (lesson.status === 'missing') {
    return (
      <Card>
        <EmptyState
          icon={GraduationCap}
          title={building ? `Writing lesson ${lesson.index + 1}…` : 'This lesson could not be written'}
          description={
            building
              ? lesson.title
              : 'The model failed on this one. Rebuilding the course (edit your goal, then Save & rebuild) usually fixes it.'
          }
        />
      </Card>
    )
  }

  return (
    <Card>
      <CardBody className="space-y-6 px-5 py-5 sm:px-8 sm:py-7">
        <header className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            Lesson {lesson.index + 1} of {lessons.length}
            {lesson.mastery != null && (
              <span className="ml-2 normal-case tracking-normal">· {Math.round(lesson.mastery * 100)}% mastery</span>
            )}
          </p>
          <h2 className="text-xl font-semibold tracking-tight">{lesson.title}</h2>
          {lesson.objective && (
            <p className="flex items-start gap-2 text-sm">
              <Target className="mt-0.5 h-4 w-4 shrink-0 text-[var(--accent)]" />
              <span>
                <span className="font-medium">By the end you can </span>
                <InlineMarkdown content={lesson.objective} />
              </span>
            </p>
          )}
        </header>

        {lesson.index > 0 && <Warmup lessonId={lesson.id} answer={answer} onCite={cite} />}

        {lesson.why && (
          <div className="flex items-start gap-2.5 rounded-lg bg-[var(--accent-soft)] px-3.5 py-2.5 text-sm">
            <Lightbulb className="mt-0.5 h-4 w-4 shrink-0 text-[var(--accent)]" />
            <span><InlineMarkdown content={lesson.why} /></span>
          </div>
        )}

        <Section icon={BookOpen} title="Learn">
          <Markdown content={lesson.body_md} onCite={cite} className="text-[15px] leading-relaxed" />
        </Section>

        {lesson.example_md && (
          <Section icon={Pencil} title="Worked example">
            <div className="rounded-lg border border-[var(--border)] px-4 py-3">
              <Markdown content={lesson.example_md} onCite={cite} className="text-sm leading-relaxed" />
            </div>
          </Section>
        )}

        <Section icon={CheckCircle2} title={`Check yourself · ${answered} of ${lesson.checks.length}`}>
          <p className="text-xs text-[var(--text-muted)]">
            Answer from memory before scrolling back up. Struggling to recall is what makes it stick.
          </p>
          {lesson.checks.length === 0 ? (
            <p className="text-sm text-[var(--text-muted)]">
              No questions: they were removed when this source's concepts were regenerated. Rebuild the course to get new ones.
            </p>
          ) : (
            <div className="space-y-3">
              {lesson.checks.map((check, index) => (
                <CheckQuestion
                  key={check.id}
                  number={index + 1}
                  item={check}
                  initial={check.result}
                  onAnswer={(response) => answer(check.id, response)}
                  onCite={cite}
                />
              ))}
            </div>
          )}
          {lesson.completed_at && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/5 px-3.5 py-2.5 text-sm">
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              <span className="font-medium">Lesson complete</span>
              {lesson.score != null && (
                <span className="text-[var(--text-muted)]">· first try {Math.round(lesson.score * 100)}%</span>
              )}
              {lesson.status === 'review' && (
                <span className="text-amber-600">· worth another pass: retry the misses, and later warm-ups will bring it back</span>
              )}
            </div>
          )}
        </Section>

        {lesson.try_it && (
          <Section icon={Hammer} title="Try it">
            <div className="rounded-lg border border-dashed border-[var(--border)] px-4 py-3 text-sm leading-relaxed">
              <Markdown content={lesson.try_it} />
            </div>
          </Section>
        )}

        <footer className="space-y-3 border-t border-[var(--border)] pt-4">
          <p className="text-xs text-[var(--text-muted)]">
            Anything unclear? Ask a follow-up in chat. It answers from this source and cites it.
          </p>
          <div className="flex flex-wrap gap-2">
            {lesson.segment_ids.length > 0 && (
              <Button size="sm" onClick={() => onJump(lesson.segment_ids)}>
                <Layers className="h-3.5 w-3.5" /> Go back to the source
              </Button>
            )}
            <Button size="sm" onClick={() => navigate({ to: '/chat', search: { source: sourceId } })}>
              <MessageSquare className="h-3.5 w-3.5" /> Ask a follow-up
            </Button>
            <span className="flex-1" />
            {following ? (
              <Button
                size="sm"
                variant={lesson.completed_at || following.id === nextLessonId ? 'primary' : 'secondary'}
                onClick={() => onView(following.id)}
              >
                Next: {following.title} <ArrowRight className="h-3.5 w-3.5" />
              </Button>
            ) : (
              <Button size="sm" variant="primary" onClick={() => onView('reference')}>
                Reference sheet <ArrowRight className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </footer>
      </CardBody>
    </Card>
  )
}

function Warmup({
  lessonId, answer, onCite,
}: {
  lessonId: number
  answer: (itemId: number, response: string) => Promise<LessonAnswer>
  onCite: (id: number) => void
}) {
  // Fetched once per visit: re-drawing after every answer would swap the question mid-attempt.
  const { data } = useQuery({
    queryKey: ['warmup', lessonId],
    queryFn: () => api.lessonWarmup(lessonId),
    staleTime: Infinity,
  })
  const items = data?.items ?? []
  if (items.length === 0) return null
  return (
    <Section icon={RotateCcw} title="Warm-up · from earlier lessons">
      <div className="space-y-3">
        {items.map((item: WarmupItem, index) => (
          <CheckQuestion
            key={item.id}
            number={index + 1}
            item={item}
            initial={null}
            label={`From lesson ${item.from_lesson.index + 1}: ${item.from_lesson.title}`}
            onAnswer={(response) => answer(item.id, response)}
            onCite={onCite}
          />
        ))}
      </div>
    </Section>
  )
}

function CheckQuestion({
  number, item, initial, label, onAnswer, onCite,
}: {
  number: number
  item: Omit<LessonCheck, 'result'>
  initial: CheckResult | null
  label?: string
  onAnswer: (response: string) => Promise<LessonAnswer>
  onCite: (id: number) => void
}) {
  const [response, setResponse] = useState(initial?.response ?? '')
  const [result, setResult] = useState<CheckResult | null>(initial)
  const [segments, setSegments] = useState<number[]>(item.segment_ids)
  const [loading, setLoading] = useState(false)
  const isChoice = item.type === 'mcq' && item.options.length > 0

  const submit = async (value: string) => {
    if (!value.trim()) return
    setLoading(true)
    try {
      const graded = await onAnswer(value)
      setResult(graded)
      setSegments(graded.segment_ids)
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Could not check that answer.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-2.5 rounded-lg border border-[var(--border)] p-3.5">
      {label && <p className="text-[11px] text-[var(--text-muted)]">{label}</p>}
      <p className="text-sm font-medium leading-relaxed">
        <span className="mr-1.5 text-[var(--text-muted)]">{number}.</span>
        <InlineMarkdown content={item.question} />
      </p>

      {isChoice ? (
        <div className="space-y-1.5">
          {item.options.map((option) => {
            const chosen = result && response === option
            const correct = result?.correct_answer === option
            return (
              <button
                key={option}
                disabled={!!result || loading}
                onClick={() => {
                  setResponse(option)
                  void submit(option)
                }}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-md border px-3 py-2 text-left text-sm transition-colors',
                  !result && 'border-[var(--border)] hover:border-[var(--accent)] hover:bg-[var(--bg-sunken)]',
                  result && correct && 'border-emerald-500 bg-emerald-500/10',
                  result && chosen && !correct && 'border-red-500 bg-red-500/10',
                  result && !correct && !chosen && 'border-[var(--border)] opacity-50',
                )}
              >
                {result && correct && <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />}
                {result && chosen && !correct && <XCircle className="h-4 w-4 shrink-0 text-red-500" />}
                <span><InlineMarkdown content={option} /></span>
              </button>
            )
          })}
        </div>
      ) : (
        <div className="space-y-2">
          <Textarea
            value={response}
            onChange={(event) => setResponse(event.target.value)}
            disabled={!!result || loading}
            rows={3}
            placeholder="In your own words…"
            onKeyDown={(event) => {
              if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) void submit(response)
            }}
          />
          {!result && (
            <Button size="sm" variant="primary" disabled={!response.trim()} loading={loading} onClick={() => submit(response)}>
              {loading ? 'Checking…' : 'Check my answer'}
            </Button>
          )}
        </div>
      )}

      {result && (
        <div
          className={cn(
            'space-y-1.5 rounded-md border p-3 text-sm',
            result.is_correct ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-amber-500/40 bg-amber-500/5',
          )}
        >
          <p className="flex items-center gap-2 font-medium">
            {result.is_correct ? (
              <><CheckCircle2 className="h-4 w-4 text-emerald-500" /> Correct</>
            ) : (
              <><XCircle className="h-4 w-4 text-amber-500" /> Not quite</>
            )}
            {!isChoice && (
              <span className="ml-auto text-xs tabular-nums text-[var(--text-muted)]">{Math.round(result.score * 100)}%</span>
            )}
          </p>
          {!isChoice && result.feedback && <p>{result.feedback}</p>}
          {!isChoice && !result.is_correct && result.correct_answer && (
            <p><span className="font-medium">A full answer: </span><InlineMarkdown content={result.correct_answer} /></p>
          )}
          {result.explanation && (
            <p className="text-xs text-[var(--text-muted)]"><InlineMarkdown content={result.explanation} /></p>
          )}
          <div className="flex flex-wrap gap-1.5 pt-1">
            {segments.length > 0 && (
              <Button size="sm" variant="ghost" onClick={() => onCite(segments[0])}>
                <Layers className="h-3.5 w-3.5" /> See it in the source
              </Button>
            )}
            {!result.is_correct && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setResult(null)
                  setResponse('')
                }}
              >
                <RotateCcw className="h-3.5 w-3.5" /> Try again
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function ReferenceSheet({ data, building, sourceId }: { data: CourseDetail; building: boolean; sourceId: number }) {
  const navigate = useNavigate()
  return (
    <Card>
      <CardBody className="space-y-5 px-5 py-5 sm:px-8 sm:py-7">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 className="text-xl font-semibold tracking-tight">Reference sheet</h2>
            <p className="text-sm text-[var(--text-muted)]">The course compressed for quick lookup. Come back to this, not the lessons.</p>
          </div>
          <Button
            size="sm"
            onClick={() =>
              navigate({ to: '/library/$sourceId', params: { sourceId: String(sourceId) }, search: { sheet: true } })
            }
          >
            <Printer className="h-3.5 w-3.5" /> Print
          </Button>
        </div>
        <ReferenceContent data={data} building={building} />
      </CardBody>
    </Card>
  )
}

function ReferenceContent({ data, building }: { data: CourseDetail; building: boolean }) {
  return (
    <>
      {data.course?.reference_md ? (
        <Markdown content={data.course.reference_md} className="text-sm leading-relaxed" />
      ) : (
        <p className="text-sm text-[var(--text-muted)]">
          {building ? 'Written after the last lesson.' : 'No reference sheet yet. It is written when the course is built.'}
        </p>
      )}
      {data.glossary.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Glossary</h3>
          <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[max-content_minmax(0,1fr)]">
            {data.glossary.map((term) => (
              <div key={term.id} className="contents">
                <dt className="font-medium">{term.name}</dt>
                <dd className="text-[var(--text-muted)]"><InlineMarkdown content={term.definition} /></dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </>
  )
}

/** The reference sheet alone, for the browser's print-to-PDF (``?sheet=true``). */
export function ReferenceSheetPrint({ sourceId, title, onBack }: { sourceId: number; title: string; onBack: () => void }) {
  const { data } = useQuery({ queryKey: ['course', sourceId], queryFn: () => api.getCourse(sourceId) })
  if (!data) return <Skeleton className="m-8 h-96" />
  return (
    <div className="mx-auto max-w-3xl space-y-5 p-8">
      <div>
        <h1 className="text-2xl font-semibold">{title}</h1>
        <p className="text-sm text-[var(--text-muted)]">Reference sheet</p>
      </div>
      <ReferenceContent data={data} building={false} />
      <div className="no-print flex gap-2 border-t border-[var(--border)] pt-4">
        <Button variant="primary" onClick={() => window.print()}>
          <Printer className="h-4 w-4" /> Print / Save as PDF
        </Button>
        <Button onClick={onBack}>Back</Button>
      </div>
    </div>
  )
}

