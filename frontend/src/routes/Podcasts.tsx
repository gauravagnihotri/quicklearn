import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Headphones, Play, Plus, Rss, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import { useJobs } from '../lib/store'
import { formatDuration, formatRelative } from '../lib/utils'
import {
  Badge, Button, Card, CardBody, Dialog, DialogContent, DialogDescription, DialogTitle,
  EmptyState, Field, Input, Progress, Select, SelectContent, SelectItem, SelectTrigger,
  SelectValue, Skeleton, Tooltip,
} from '../components/ui'

export function PodcastsPage() {
  const [createOpen, setCreateOpen] = useState(false)
  const queryClient = useQueryClient()
  const jobs = useJobs((state) => state.jobs)
  const busy = Object.values(jobs).some(
    (job) => job.type === 'generate_podcast' && (job.status === 'running' || job.status === 'queued'),
  )

  const { data, isLoading } = useQuery({
    queryKey: ['podcasts'],
    queryFn: () => api.listPodcasts(),
    refetchInterval: busy ? 3000 : false,
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.deletePodcast(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['podcasts'] }),
  })

  const regenerate = useMutation({
    mutationFn: (id: number) => api.regeneratePodcast(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['podcasts'] })
      toast.success('Regenerating — unchanged lines are reused from cache.')
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed.'),
  })

  const podcasts = data?.podcasts ?? []

  return (
    <div className="mx-auto max-w-4xl p-4 sm:p-6">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Podcasts</h1>
          <p className="text-sm text-[var(--text-muted)]">
            Two-host discussions generated from your notes. Listen on the commute.
          </p>
        </div>
        <div className="flex gap-2">
          <Tooltip label="Subscribe in any podcast app">
            <a href={api.feedUrl()} target="_blank" rel="noreferrer">
              <Button>
                <Rss className="h-4 w-4" /> RSS
              </Button>
            </a>
          </Tooltip>
          <Button variant="primary" onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" /> New podcast
          </Button>
        </div>
      </header>

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
        </div>
      ) : podcasts.length === 0 ? (
        <Card>
          <EmptyState
            icon={Headphones}
            title="No podcasts yet"
            description="Turn a source — or a whole week of them — into a conversation between two hosts, one explaining and one asking the questions you'd ask."
            action={
              <Button variant="primary" onClick={() => setCreateOpen(true)}>
                Generate one
              </Button>
            }
          />
        </Card>
      ) : (
        <div className="space-y-3">
          {podcasts.map((podcast) => {
            const job = Object.values(jobs).find(
              (candidate) =>
                candidate.type === 'generate_podcast' &&
                (candidate.status === 'running' || candidate.status === 'queued'),
            )
            const generating = podcast.status !== 'ready' && podcast.status !== 'failed'
            return (
              <Card key={podcast.id}>
                <CardBody className="space-y-3">
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{podcast.title}</p>
                      <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
                        <Badge
                          tone={
                            podcast.status === 'ready'
                              ? 'success'
                              : podcast.status === 'failed'
                                ? 'danger'
                                : 'accent'
                          }
                        >
                          {podcast.status}
                        </Badge>
                        {podcast.duration_sec ? (
                          <span>{formatDuration(podcast.duration_sec)}</span>
                        ) : (
                          <span>~{podcast.target_minutes} min target</span>
                        )}
                        {podcast.script.length > 0 && <span>· {podcast.script.length} exchanges</span>}
                        <span>· {formatRelative(podcast.created_at)}</span>
                      </div>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      {podcast.status === 'failed' && (
                        <Button size="sm" onClick={() => regenerate.mutate(podcast.id)}>
                          Retry
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="text-red-500"
                        onClick={() => {
                          if (confirm(`Delete “${podcast.title}”?`)) remove.mutate(podcast.id)
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>

                  {generating && (
                    <div>
                      <Progress value={job?.progress ?? 15} />
                      <p className="mt-1 text-[11px] text-[var(--text-muted)]">
                        {job?.stage || 'Working…'}
                      </p>
                    </div>
                  )}

                  {podcast.status === 'failed' && podcast.error && (
                    <p className="text-xs text-red-500">{podcast.error}</p>
                  )}

                  {podcast.has_audio && (
                    <audio
                      controls
                      preload="none"
                      className="w-full"
                      src={api.podcastAudioUrl(podcast.id)}
                    />
                  )}

                  {podcast.script.length > 0 && <ScriptPreview script={podcast.script} />}
                </CardBody>
              </Card>
            )
          })}
        </div>
      )}

      <CreatePodcastDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  )
}

function ScriptPreview({ script }: { script: { speaker: string; text: string }[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-t border-[var(--border)] pt-2">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs text-[var(--accent)] hover:underline"
      >
        {open ? 'Hide' : 'Show'} transcript
      </button>
      {open && (
        <div className="mt-2 max-h-72 space-y-2 overflow-y-auto rounded-lg bg-[var(--bg-sunken)] p-3">
          {script.map((line, index) => (
            <p key={index} className="text-sm">
              <span
                className={
                  line.speaker === 'Host A'
                    ? 'font-semibold text-[var(--accent)]'
                    : 'font-semibold text-emerald-600 dark:text-emerald-400'
                }
              >
                {line.speaker}:{' '}
              </span>
              {line.text}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

function CreatePodcastDialog({
  open, onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [selected, setSelected] = useState<number[]>([])
  const [length, setLength] = useState('10')
  const [title, setTitle] = useState('')
  const [voiceA, setVoiceA] = useState('')
  const [voiceB, setVoiceB] = useState('')
  const queryClient = useQueryClient()

  const { data: sources } = useQuery({
    queryKey: ['sources'],
    queryFn: () => api.listSources({ status: 'ready' }),
  })
  const { data: voiceData } = useQuery({
    queryKey: ['voices'],
    queryFn: () => api.podcastVoices(),
    enabled: open,
  })

  const create = useMutation({
    mutationFn: () =>
      api.createPodcast({
        source_ids: selected,
        length: Number(length),
        title: title.trim(),
        voice_a: voiceA,
        voice_b: voiceB,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['podcasts'] })
      onOpenChange(false)
      setSelected([])
      setTitle('')
      toast.success('Generating — this takes a few minutes.')
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed.'),
  })

  const voices = voiceData?.voices ?? []
  const ready = sources?.sources ?? []

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>New podcast</DialogTitle>
        <DialogDescription>
          Pick one source, or several to cover a week of material in one listen.
        </DialogDescription>

        {voiceData?.error && (
          <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs">
            No text-to-speech provider is configured.{' '}
            <Link to="/settings" className="underline">
              Add one in Settings
            </Link>{' '}
            to generate podcasts.
          </div>
        )}

        <div className="space-y-4">
          <Field label="Sources" hint={`${selected.length} selected`}>
            <div className="max-h-52 space-y-1 overflow-y-auto rounded-lg border border-[var(--border)] p-1.5">
              {ready.length === 0 && (
                <p className="p-2 text-xs text-[var(--text-muted)]">
                  No processed sources yet.
                </p>
              )}
              {ready.map((source) => (
                <label
                  key={source.id}
                  className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-[var(--bg-sunken)]"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(source.id)}
                    onChange={(event) =>
                      setSelected((current) =>
                        event.target.checked
                          ? [...current, source.id]
                          : current.filter((id) => id !== source.id),
                      )
                    }
                    className="accent-[var(--accent)]"
                  />
                  <span className="min-w-0 flex-1 truncate text-sm">{source.title}</span>
                </label>
              ))}
            </div>
          </Field>

          <Field label="Title" hint="Leave blank to name it after the source.">
            <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Optional" />
          </Field>

          <Field label="Length">
            <Select value={length} onValueChange={setLength}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="5">5 minutes</SelectItem>
                <SelectItem value="10">10 minutes</SelectItem>
                <SelectItem value="20">20 minutes</SelectItem>
              </SelectContent>
            </Select>
          </Field>

          {voices.length > 0 && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Host A — explains">
                <Select value={voiceA} onValueChange={setVoiceA}>
                  <SelectTrigger>
                    <SelectValue placeholder="Default" />
                  </SelectTrigger>
                  <SelectContent>
                    {voices.map((voice) => (
                      <SelectItem key={voice.id} value={voice.id}>
                        {voice.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Host B — asks">
                <Select value={voiceB} onValueChange={setVoiceB}>
                  <SelectTrigger>
                    <SelectValue placeholder="Default" />
                  </SelectTrigger>
                  <SelectContent>
                    {voices.map((voice) => (
                      <SelectItem key={voice.id} value={voice.id}>
                        {voice.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
          )}

          <Button
            variant="primary"
            className="w-full justify-center"
            disabled={selected.length === 0}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            <Play className="h-4 w-4" /> Generate
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
