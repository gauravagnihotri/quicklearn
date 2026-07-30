import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import {
  AlertTriangle, Brain, Flame, Mic, Plus, Sparkles, TrendingDown, Upload, Wallet,
} from 'lucide-react'
import { api } from '../lib/api'
import { useJobs } from '../lib/store'
import { formatCost, formatRelative, kindLabel } from '../lib/utils'
import { AddSourceDialog } from '../components/AddSourceDialog'
import { Badge, Button, Card, CardBody, EmptyState, Progress, Skeleton } from '../components/ui'

export function DashboardPage() {
  const navigate = useNavigate()
  const jobs = useJobs((state) => state.jobs)
  const activeCount = Object.values(jobs).filter(
    (job) => job.parent_id == null && (job.status === 'running' || job.status === 'queued'),
  ).length

  const { data, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.dashboard(),
    // Poll while work is in flight so counts settle without a manual refresh.
    refetchInterval: activeCount > 0 ? 2500 : 30_000,
  })

  if (isLoading || !data) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-6">
        <Skeleton className="h-9 w-52" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    )
  }

  const noProviders = !data.providers_configured.llm
  const dueTotal = data.due.due + data.due.new

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-4 sm:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-[var(--text-muted)]">
            {data.totals.sources === 0
              ? 'Add your first lecture, paper, or recording to get started.'
              : `${data.totals.sources} source${data.totals.sources === 1 ? '' : 's'} · ${data.totals.cards} cards · ${data.totals.concepts} concepts`}
          </p>
        </div>
        <div className="flex gap-2">
          <Link to="/record">
            <Button variant="secondary">
              <Mic className="h-4 w-4" /> Record
            </Button>
          </Link>
          <AddSourceDialog
            trigger={
              <Button variant="primary">
                <Plus className="h-4 w-4" /> Add source
              </Button>
            }
          />
        </div>
      </header>

      {noProviders && (
        <Card className="border-amber-500/40 bg-amber-500/5">
          <CardBody className="flex flex-wrap items-center gap-3">
            <AlertTriangle className="h-5 w-5 shrink-0 text-amber-500" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">No language model configured yet</p>
              <p className="text-xs text-[var(--text-muted)]">
                Add an API key to generate notes and flashcards — Groq has a free tier. Or pick
                the offline mock provider to look around first.
              </p>
            </div>
            <Link to="/settings">
              <Button variant="primary" size="sm">Open settings</Button>
            </Link>
          </CardBody>
        </Card>
      )}

      {data.unfinished_recordings > 0 && (
        <Card className="border-[var(--accent)]/40 bg-[var(--accent-soft)]">
          <CardBody className="flex flex-wrap items-center gap-3">
            <Mic className="h-5 w-5 shrink-0 text-[var(--accent)]" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">
                {data.unfinished_recordings} unfinished recording
                {data.unfinished_recordings === 1 ? '' : 's'}
              </p>
              <p className="text-xs text-[var(--text-muted)]">
                A recording was interrupted. The audio is safe on disk — process it anyway?
              </p>
            </div>
            <Link to="/record">
              <Button size="sm" variant="primary">Review</Button>
            </Link>
          </CardBody>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Due today"
          value={dueTotal}
          hint={`${data.due.due} review · ${data.due.new} new`}
          icon={Brain}
          onClick={() => navigate({ to: '/review' })}
          accent={dueTotal > 0}
        />
        <StatCard
          label="Study streak"
          value={data.stats.streak_days}
          hint={data.stats.streak_days === 1 ? 'day' : 'days'}
          icon={Flame}
        />
        <StatCard
          label="Mastery"
          value={`${Math.round(data.mastery.average_mastery * 100)}%`}
          hint={`${data.mastery.mastered}/${data.mastery.concepts} concepts mastered`}
          icon={Sparkles}
        />
        <StatCard
          label="Spend to date"
          value={formatCost(data.usage.total_usd)}
          hint={`${data.usage.calls} API calls`}
          icon={Wallet}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
            <h2 className="text-sm font-semibold">Recent sources</h2>
            <Link to="/library" className="text-xs text-[var(--accent)] hover:underline">
              View all
            </Link>
          </div>
          {data.recent_sources.length === 0 ? (
            <EmptyState
              icon={Upload}
              title="Nothing here yet"
              description="Upload a lecture recording, drop in a PDF, paste a YouTube link, or record straight from your browser."
              action={<AddSourceDialog trigger={<Button variant="primary">Add your first source</Button>} />}
            />
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {data.recent_sources.map((source) => (
                <li key={source.id}>
                  <Link
                    to="/library/$sourceId"
                    params={{ sourceId: String(source.id) }}
                    className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-[var(--bg-sunken)]"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{source.title}</p>
                      <p className="text-xs text-[var(--text-muted)]">
                        {kindLabel(source.kind)} · {formatRelative(source.created_at)}
                      </p>
                    </div>
                    <StatusBadge status={source.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <div className="flex items-center gap-2 border-b border-[var(--border)] px-4 py-3">
            <TrendingDown className="h-4 w-4 text-[var(--text-muted)]" />
            <h2 className="text-sm font-semibold">Weakest concepts</h2>
          </div>
          {data.weak_concepts.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-[var(--text-muted)]">
              {data.totals.concepts === 0
                ? 'Concepts appear once a source has been processed.'
                : 'Everything is above the mastery threshold. Nice.'}
            </p>
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {data.weak_concepts.map((concept) => (
                <li key={concept.id} className="px-4 py-2.5">
                  <div className="flex items-baseline justify-between gap-2">
                    <p className="truncate text-sm font-medium">{concept.name}</p>
                    <span className="shrink-0 tabular-nums text-xs text-[var(--text-muted)]">
                      {Math.round(concept.mastery * 100)}%
                    </span>
                  </div>
                  <Progress
                    value={concept.mastery * 100}
                    tone={concept.mastery < 0.35 ? 'danger' : 'accent'}
                    className="mt-1.5"
                  />
                  <p className="mt-1 truncate text-[11px] text-[var(--text-muted)]">
                    {concept.source_title}
                  </p>
                </li>
              ))}
            </ul>
          )}
          {data.weak_concepts.length > 0 && (
            <div className="border-t border-[var(--border)] p-3">
              <Link to="/quiz">
                <Button variant="secondary" size="sm" className="w-full justify-center">
                  Practise these
                </Button>
              </Link>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

function StatCard({
  label, value, hint, icon: Icon, onClick, accent,
}: {
  label: string
  value: string | number
  hint?: string
  icon: React.ComponentType<{ className?: string }>
  onClick?: () => void
  accent?: boolean
}) {
  const inner = (
    <CardBody className="flex items-start gap-3">
      <span
        className={
          accent
            ? 'rounded-lg bg-[var(--accent-soft)] p-2 text-[var(--accent)]'
            : 'rounded-lg bg-[var(--bg-sunken)] p-2 text-[var(--text-muted)]'
        }
      >
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <p className="text-xs text-[var(--text-muted)]">{label}</p>
        <p className="text-xl font-semibold tabular-nums">{value}</p>
        {hint && <p className="truncate text-[11px] text-[var(--text-muted)]">{hint}</p>}
      </div>
    </CardBody>
  )
  return onClick ? (
    <Card className="cursor-pointer transition-colors hover:border-[var(--accent)]/40" onClick={onClick}>
      {inner}
    </Card>
  ) : (
    <Card>{inner}</Card>
  )
}

export function StatusBadge({ status }: { status: string }) {
  if (status === 'ready') return <Badge tone="success">ready</Badge>
  if (status === 'failed') return <Badge tone="danger">failed</Badge>
  if (status === 'processing') return <Badge tone="accent">processing</Badge>
  if (status === 'recording') return <Badge tone="warning">recording</Badge>
  return <Badge>{status}</Badge>
}
