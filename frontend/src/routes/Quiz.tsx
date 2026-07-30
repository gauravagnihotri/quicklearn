import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useSearch } from '@tanstack/react-router'
import { CheckCircle2, ListChecks, Target, TrendingDown, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import type { AnswerResult, QuizQuestion, QuizResults } from '../lib/types'
import { cn } from '../lib/utils'
import {
  Badge, Button, Card, CardBody, EmptyState, Field, Progress, Select, SelectContent,
  SelectItem, SelectTrigger, SelectValue, Skeleton, Spinner, Textarea,
} from '../components/ui'

type Mode = 'practice' | 'test' | 'cumulative'

const MODES: { value: Mode; label: string; hint: string }[] = [
  {
    value: 'practice',
    label: 'Practice',
    hint: 'Endless and adaptive. Targets your weakest concepts and writes fresh questions each time, so you cannot memorise the bank.',
  },
  {
    value: 'test',
    label: 'Test',
    hint: 'Fixed length, scored, no feedback until you finish.',
  },
  {
    value: 'cumulative',
    label: 'Cumulative',
    hint: 'Spans a whole folder — the exam-prep option.',
  },
]

export function QuizPage() {
  const search = useSearch({ from: '/quiz' })
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [mode, setMode] = useState<Mode>((search.mode as Mode) ?? 'practice')

  if (sessionId === null) {
    return (
      <QuizSetup
        mode={mode}
        setMode={setMode}
        presetSource={search.source}
        presetFolder={search.folder}
        onStart={setSessionId}
      />
    )
  }
  return <QuizRunner sessionId={sessionId} mode={mode} onExit={() => setSessionId(null)} />
}

function QuizSetup({
  mode, setMode, presetSource, presetFolder, onStart,
}: {
  mode: Mode
  setMode: (mode: Mode) => void
  presetSource?: number
  presetFolder?: number
  onStart: (sessionId: number) => void
}) {
  const [sourceId, setSourceId] = useState<string>(presetSource ? String(presetSource) : '')
  const [folderId, setFolderId] = useState<string>(presetFolder ? String(presetFolder) : '')
  const [length, setLength] = useState('10')

  const { data: sources, isLoading } = useQuery({
    queryKey: ['sources'],
    queryFn: () => api.listSources({ status: 'ready' }),
  })
  const { data: folders } = useQuery({ queryKey: ['folders'], queryFn: () => api.listFolders() })

  const start = useMutation({
    mutationFn: () =>
      api.createQuizSession({
        mode,
        source_ids: mode === 'cumulative' ? [] : sourceId ? [Number(sourceId)] : [],
        folder_id: mode === 'cumulative' && folderId ? Number(folderId) : null,
        length: mode === 'practice' ? null : Number(length),
      }),
    onSuccess: (result) => onStart(result.session_id),
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not start the quiz.'),
  })

  if (isLoading) {
    return (
      <div className="mx-auto max-w-2xl space-y-3 p-6">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-64" />
      </div>
    )
  }

  const ready = sources?.sources ?? []
  if (ready.length === 0) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <Card>
          <EmptyState
            icon={ListChecks}
            title="Nothing to quiz on yet"
            description="Add and process a source first — quiz questions are generated from its extracted concepts."
            action={
              <Link to="/library">
                <Button variant="primary">Go to library</Button>
              </Link>
            }
          />
        </Card>
      </div>
    )
  }

  const canStart = mode === 'cumulative' ? !!folderId : !!sourceId

  return (
    <div className="mx-auto max-w-2xl p-4 sm:p-6">
      <h1 className="mb-1 text-2xl font-semibold tracking-tight">Quiz</h1>
      <p className="mb-5 text-sm text-[var(--text-muted)]">
        Questions are written from the concepts in your material, with distractors drawn from
        neighbouring ideas rather than random ones.
      </p>

      <div className="mb-4 grid gap-2 sm:grid-cols-3">
        {MODES.map((option) => (
          <button
            key={option.value}
            onClick={() => setMode(option.value)}
            className={cn(
              'rounded-xl border p-3 text-left transition-colors',
              mode === option.value
                ? 'border-[var(--accent)] bg-[var(--accent-soft)]'
                : 'border-[var(--border)] hover:bg-[var(--bg-sunken)]',
            )}
          >
            <p className="text-sm font-medium">{option.label}</p>
            <p className="mt-0.5 text-[11px] leading-snug text-[var(--text-muted)]">{option.hint}</p>
          </button>
        ))}
      </div>

      <Card>
        <CardBody className="space-y-4">
          {mode === 'cumulative' ? (
            <Field label="Folder" hint="Every source in this folder and its subfolders.">
              <Select value={folderId} onValueChange={setFolderId}>
                <SelectTrigger>
                  <SelectValue placeholder="Choose a folder" />
                </SelectTrigger>
                <SelectContent>
                  {(folders?.folders ?? []).map((folder) => (
                    <SelectItem key={folder.id} value={String(folder.id)}>
                      {folder.name} ({folder.source_count})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          ) : (
            <Field label="Source">
              <Select value={sourceId} onValueChange={setSourceId}>
                <SelectTrigger>
                  <SelectValue placeholder="Choose a source" />
                </SelectTrigger>
                <SelectContent>
                  {ready.map((source) => (
                    <SelectItem key={source.id} value={String(source.id)}>
                      {source.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}

          {mode !== 'practice' && (
            <Field label="Number of questions">
              <Select value={length} onValueChange={setLength}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {['5', '10', '15', '20', '30'].map((value) => (
                    <SelectItem key={value} value={value}>
                      {value} questions
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}

          <Button
            variant="primary"
            size="lg"
            className="w-full justify-center"
            disabled={!canStart}
            loading={start.isPending}
            onClick={() => start.mutate()}
          >
            Start {MODES.find((m) => m.value === mode)?.label.toLowerCase()}
          </Button>
        </CardBody>
      </Card>
    </div>
  )
}

function QuizRunner({
  sessionId, mode, onExit,
}: {
  sessionId: number
  mode: Mode
  onExit: () => void
}) {
  const [question, setQuestion] = useState<QuizQuestion | null>(null)
  const [loading, setLoading] = useState(true)
  const [finished, setFinished] = useState<string | null>(null)
  const [response, setResponse] = useState('')
  const [result, setResult] = useState<AnswerResult | null>(null)
  const [results, setResults] = useState<QuizResults | null>(null)
  const [count, setCount] = useState(0)

  const loadNext = async () => {
    setLoading(true)
    setResult(null)
    setResponse('')
    try {
      const next = await api.nextQuestion(sessionId)
      if (next.done) {
        setFinished(next.reason)
        setResults(await api.quizResults(sessionId))
      } else {
        setQuestion(next)
      }
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Could not load the next question.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadNext()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  const submit = async (answer: string) => {
    if (!question || !answer.trim()) return
    setLoading(true)
    try {
      const outcome = await api.answerQuestion(sessionId, question.id, answer)
      setResult(outcome)
      setCount((c) => c + 1)
      // Test mode withholds feedback, so there is nothing to pause on — move straight on.
      if (mode === 'test') await loadNext()
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Could not submit that answer.')
    } finally {
      setLoading(false)
    }
  }

  const finish = async () => {
    try {
      setResults(await api.quizResults(sessionId))
      setFinished('Session ended.')
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Could not load results.')
    }
  }

  if (finished && results) {
    return <QuizSummary results={results} reason={finished} onExit={onExit} />
  }

  if (!question || (loading && !result)) {
    return (
      <div className="mx-auto flex max-w-2xl items-center justify-center gap-2 p-16 text-sm text-[var(--text-muted)]">
        <Spinner /> {mode === 'practice' ? 'Writing your next question…' : 'Loading…'}
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl p-4 sm:p-6">
      <div className="mb-3 flex items-center gap-3 text-xs text-[var(--text-muted)]">
        <span className="tabular-nums">Question {count + 1}</span>
        {question.concept && (
          <Badge tone={question.concept.mastery >= 0.8 ? 'success' : 'neutral'}>
            {question.concept.name} · {Math.round(question.concept.mastery * 100)}%
          </Badge>
        )}
        <div className="flex-1" />
        <Button variant="ghost" size="sm" onClick={finish}>
          Finish
        </Button>
      </div>

      {mode === 'practice' && question.progress && (
        <div className="mb-3">
          <div className="mb-1 flex justify-between text-[11px] text-[var(--text-muted)]">
            <span>
              {question.progress.mastered} of {question.progress.concepts} concepts mastered
            </span>
            <span>{Math.round(question.progress.average_mastery * 100)}% average</span>
          </div>
          <Progress
            value={(question.progress.mastered / Math.max(1, question.progress.concepts)) * 100}
            tone="success"
          />
        </div>
      )}

      <Card>
        <CardBody className="space-y-4">
          <div className="flex items-start gap-2">
            <Badge>{question.type}</Badge>
            <Badge tone="neutral">difficulty {question.difficulty}</Badge>
          </div>

          <p className="text-lg leading-relaxed">{question.question}</p>

          {question.type === 'mcq' ? (
            <div className="space-y-2">
              {question.options.map((option) => {
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
                      'flex w-full items-center gap-2.5 rounded-lg border px-3.5 py-2.5 text-left text-sm transition-colors',
                      !result && 'border-[var(--border)] hover:border-[var(--accent)] hover:bg-[var(--bg-sunken)]',
                      result && correct && 'border-emerald-500 bg-emerald-500/10',
                      result && chosen && !correct && 'border-red-500 bg-red-500/10',
                      result && !correct && !chosen && 'border-[var(--border)] opacity-50',
                    )}
                  >
                    {result && correct && <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />}
                    {result && chosen && !correct && <XCircle className="h-4 w-4 shrink-0 text-red-500" />}
                    <span>{option}</span>
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
                rows={question.type === 'free' ? 6 : 2}
                placeholder={
                  question.type === 'cloze'
                    ? 'The missing term…'
                    : question.type === 'short'
                      ? 'A sentence or a formula…'
                      : 'Explain your reasoning…'
                }
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                    void submit(response)
                  }
                }}
              />
              {!result && (
                <Button
                  variant="primary"
                  className="w-full justify-center"
                  disabled={!response.trim()}
                  loading={loading}
                  onClick={() => submit(response)}
                >
                  Submit answer
                </Button>
              )}
            </div>
          )}

          {result && mode !== 'test' && (
            <div
              className={cn(
                'animate-in-up space-y-2 rounded-lg border p-3.5',
                result.is_correct
                  ? 'border-emerald-500/40 bg-emerald-500/5'
                  : 'border-amber-500/40 bg-amber-500/5',
              )}
            >
              <p className="flex items-center gap-2 text-sm font-medium">
                {result.is_correct ? (
                  <>
                    <CheckCircle2 className="h-4 w-4 text-emerald-500" /> Correct
                  </>
                ) : (
                  <>
                    <XCircle className="h-4 w-4 text-amber-500" /> Not quite
                  </>
                )}
                <span className="ml-auto text-xs tabular-nums text-[var(--text-muted)]">
                  {Math.round(result.score * 100)}%
                </span>
              </p>
              {result.feedback && <p className="text-sm">{result.feedback}</p>}
              {!result.is_correct && result.correct_answer && question.type !== 'mcq' && (
                <p className="text-sm">
                  <span className="font-medium">Answer: </span>
                  {result.correct_answer}
                </p>
              )}
              {result.explanation && (
                <p className="border-t border-current/10 pt-2 text-xs text-[var(--text-muted)]">
                  {result.explanation}
                </p>
              )}
              {result.mastery !== undefined && (
                <div className="flex items-center gap-2 pt-1">
                  <Target className="h-3 w-3 text-[var(--text-muted)]" />
                  <Progress value={result.mastery * 100} className="flex-1" tone="success" />
                  <span className="text-[11px] tabular-nums text-[var(--text-muted)]">
                    {Math.round(result.mastery * 100)}%
                  </span>
                </div>
              )}
            </div>
          )}
        </CardBody>
      </Card>

      {result && mode !== 'test' && (
        <Button
          variant="primary"
          size="lg"
          className="mt-3 w-full justify-center"
          loading={loading}
          onClick={loadNext}
        >
          Next question
        </Button>
      )}
    </div>
  )
}

function QuizSummary({
  results, reason, onExit,
}: {
  results: QuizResults
  reason: string
  onExit: () => void
}) {
  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4 sm:p-6">
      <Card>
        <CardBody className="text-center">
          <p className="text-sm text-[var(--text-muted)]">{reason}</p>
          <p className="mt-2 text-4xl font-semibold tabular-nums">
            {Math.round(results.accuracy * 100)}%
          </p>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            {results.correct} of {results.answered} correct
          </p>
        </CardBody>
      </Card>

      {results.weak_concepts.length > 0 && (
        <Card>
          <div className="flex items-center gap-2 border-b border-[var(--border)] px-4 py-3">
            <TrendingDown className="h-4 w-4 text-[var(--text-muted)]" />
            <h2 className="text-sm font-semibold">Worth another look</h2>
          </div>
          <ul className="divide-y divide-[var(--border)]">
            {results.weak_concepts.map((concept) => (
              <li key={concept.id} className="px-4 py-3">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="font-medium">{concept.name}</p>
                  <span className="shrink-0 tabular-nums text-xs text-[var(--text-muted)]">
                    {Math.round(concept.mastery * 100)}%
                  </span>
                </div>
                <p className="mt-0.5 text-sm text-[var(--text-muted)]">{concept.definition}</p>
                <Link
                  to="/library/$sourceId"
                  params={{ sourceId: String(concept.source_id) }}
                  search={{ tab: 'transcript', seg: concept.segment_ids[0] }}
                  className="mt-1.5 inline-block text-xs text-[var(--accent)] hover:underline"
                >
                  Open in {concept.source_title} →
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {results.items.length > 0 && (
        <Card>
          <div className="border-b border-[var(--border)] px-4 py-3">
            <h2 className="text-sm font-semibold">Your answers</h2>
          </div>
          <ul className="divide-y divide-[var(--border)]">
            {results.items.map((item, index) => (
              <li key={index} className="px-4 py-3">
                <div className="flex items-start gap-2">
                  {item.is_correct ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                  ) : (
                    <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{item.question}</p>
                    <p className="mt-1 text-xs text-[var(--text-muted)]">
                      You said: {item.response || '(blank)'}
                    </p>
                    {!item.is_correct && (
                      <p className="text-xs text-emerald-600 dark:text-emerald-400">
                        Answer: {item.correct_answer}
                      </p>
                    )}
                    {item.feedback && <p className="mt-1 text-xs">{item.feedback}</p>}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="flex gap-2">
        <Button variant="primary" className="flex-1 justify-center" onClick={onExit}>
          New quiz
        </Button>
        <Link to="/" className="flex-1">
          <Button className="w-full justify-center">Dashboard</Button>
        </Link>
      </div>
    </div>
  )
}
