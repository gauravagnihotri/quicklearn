import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check, CheckCircle2, Cpu, Eye, EyeOff, Key, Moon, Plug, Sun, Wallet, XCircle, Zap,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import { useTheme } from '../lib/store'
import type { ModelInfo, ProviderInfo } from '../lib/types'
import { cn, formatCost, formatRelative } from '../lib/utils'
import {
  Badge, Button, Card, CardBody, Field, Input, Select, SelectContent, SelectItem,
  SelectTrigger, SelectValue, Skeleton, Switch, Tabs, TabsContent, TabsList, TabsTrigger,
} from '../components/ui'

const TASK_CLASSES = [
  { key: 'fast', label: 'Fast', hint: 'Relevance filtering, reranking, titles. Use the cheapest model you have — this runs hundreds of times per source.' },
  { key: 'standard', label: 'Standard', hint: 'Notes, concepts, flashcards, quizzes. The workhorse.' },
  { key: 'deep', label: 'Deep', hint: 'Podcast scripting and complex synthesis. Your best model.' },
] as const

export function SettingsPage() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['settings'], queryFn: () => api.settings() })

  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.saveSettings(values),
    onSuccess: () => {
      queryClient.invalidateQueries()
      toast.success('Saved.')
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Could not save.'),
  })

  if (isLoading || !data) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 p-6">
        <Skeleton className="h-9 w-40" />
        <Skeleton className="h-96" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl p-4 sm:p-6">
      <h1 className="mb-1 text-2xl font-semibold tracking-tight">Settings</h1>
      <p className="mb-5 text-sm text-[var(--text-muted)]">
        Everything is configured here — there are no config files to edit. Keys are encrypted
        at rest and never sent to your browser.
      </p>

      <Tabs defaultValue="providers">
        <TabsList className="mb-4">
          <TabsTrigger value="providers">
            <Plug className="h-3.5 w-3.5" /> Providers
          </TabsTrigger>
          <TabsTrigger value="routing">
            <Cpu className="h-3.5 w-3.5" /> Model routing
          </TabsTrigger>
          <TabsTrigger value="generation">
            <Zap className="h-3.5 w-3.5" /> Generation
          </TabsTrigger>
          <TabsTrigger value="usage">
            <Wallet className="h-3.5 w-3.5" /> Usage
          </TabsTrigger>
        </TabsList>

        <TabsContent value="providers" className="space-y-3">
          {!data.configured.llm && (
            <Card className="border-amber-500/40 bg-amber-500/5">
              <CardBody className="py-3 text-sm">
                <p className="font-medium">No language model configured</p>
                <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                  Add a key below, then choose models under <strong>Model routing</strong>.
                  Groq is free to start and covers both transcription and text generation.
                  Or select <strong>Mock (offline demo)</strong> to explore with no key at all.
                </p>
              </CardBody>
            </Card>
          )}
          {data.providers.map((provider) => (
            <ProviderCard key={provider.name} provider={provider} />
          ))}
        </TabsContent>

        <TabsContent value="routing" className="space-y-4">
          <RoutingSection settings={data.settings} onSave={(values) => save.mutate(values)} />
        </TabsContent>

        <TabsContent value="generation" className="space-y-4">
          <GenerationSection settings={data.settings} onSave={(values) => save.mutate(values)} />
        </TabsContent>

        <TabsContent value="usage">
          <UsageSection />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function ProviderCard({ provider }: { provider: ProviderInfo }) {
  const queryClient = useQueryClient()
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState(provider.base_url)
  const [reveal, setReveal] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string; models: ModelInfo[] } | null>(
    null,
  )

  const test = useMutation({
    mutationFn: () =>
      api.testProvider({
        provider: provider.name,
        kind: provider.kinds[0] ?? 'llm',
        api_key: apiKey,
        base_url: baseUrl,
      }),
    onSuccess: setResult,
    onError: (error) =>
      setResult({
        ok: false,
        message: error instanceof ApiError ? error.message : 'Test failed.',
        models: [],
      }),
  })

  const save = useMutation({
    mutationFn: () => {
      const values: Record<string, unknown> = {}
      if (apiKey) values[`providers.${provider.name}.api_key`] = apiKey
      if (baseUrl !== provider.base_url) values[`providers.${provider.name}.base_url`] = baseUrl
      return api.saveSettings(values)
    },
    onSuccess: () => {
      queryClient.invalidateQueries()
      setApiKey('')
      toast.success(`${provider.label} saved.`)
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed.'),
  })

  const clear = useMutation({
    mutationFn: () => api.saveSettings({ [`providers.${provider.name}.api_key`]: '' }),
    onSuccess: () => {
      queryClient.invalidateQueries()
      toast.success('Key removed.')
    },
  })

  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex items-center gap-2">
          <span className="font-medium">{provider.label}</span>
          {provider.is_local && <Badge tone="success">local · $0</Badge>}
          {provider.has_key && <Badge tone="accent">key saved</Badge>}
          <div className="flex-1" />
          <div className="flex gap-1">
            {provider.kinds.map((kind) => (
              <Badge key={kind}>{kind}</Badge>
            ))}
          </div>
        </div>

        {provider.requires_api_key && (
          <Field label="API key">
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Key className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--text-muted)]" />
                <Input
                  type={reveal ? 'text' : 'password'}
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={provider.has_key ? '•••••••• (saved)' : 'Paste your key'}
                  className="pl-8 pr-9"
                  autoComplete="off"
                />
                <button
                  type="button"
                  onClick={() => setReveal(!reveal)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--text-muted)] hover:text-[var(--text)]"
                  aria-label={reveal ? 'Hide' : 'Show'}
                >
                  {reveal ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
              {provider.has_key && (
                <Button variant="ghost" onClick={() => clear.mutate()}>
                  Remove
                </Button>
              )}
            </div>
          </Field>
        )}

        {(provider.is_local || provider.default_base_url) && (
          <Field label="Base URL" hint={provider.default_base_url || undefined}>
            <Input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder={provider.default_base_url}
            />
          </Field>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            size="sm"
            disabled={!apiKey && baseUrl === provider.base_url}
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            Save
          </Button>
          <Button size="sm" loading={test.isPending} onClick={() => test.mutate()}>
            Test connection
          </Button>
        </div>

        {result && (
          <div
            className={cn(
              'flex items-start gap-2 rounded-lg border p-2.5 text-xs',
              result.ok
                ? 'border-emerald-500/40 bg-emerald-500/5'
                : 'border-red-500/40 bg-red-500/5',
            )}
          >
            {result.ok ? (
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
            ) : (
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-500" />
            )}
            <div className="min-w-0">
              <p>{result.message}</p>
              {result.models.length > 0 && (
                <p className="mt-1 text-[11px] text-[var(--text-muted)]">
                  {result.models.slice(0, 6).map((model) => model.id).join(', ')}
                  {result.models.length > 6 && ` +${result.models.length - 6} more`}
                </p>
              )}
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  )
}

function RoutingSection({
  settings, onSave,
}: {
  settings: Record<string, unknown>
  onSave: (values: Record<string, unknown>) => void
}) {
  const { data: llmModels } = useQuery({
    queryKey: ['models', 'llm'],
    queryFn: () => api.models('llm'),
  })
  const { data: sttModels } = useQuery({
    queryKey: ['models', 'stt'],
    queryFn: () => api.models('stt'),
  })
  const { data: ttsModels } = useQuery({
    queryKey: ['models', 'tts'],
    queryFn: () => api.models('tts'),
  })

  const options = (
    payload: typeof llmModels,
  ): { provider: string; model: string; label: string }[] => {
    const output: { provider: string; model: string; label: string }[] = []
    for (const [provider, info] of Object.entries(payload?.providers ?? {})) {
      if (!info.ok) continue
      for (const model of info.models) {
        output.push({ provider, model: model.id, label: `${provider} · ${model.id}` })
      }
    }
    return output
  }

  const llmOptions = options(llmModels)
  const sttOptions = options(sttModels)
  const ttsOptions = options(ttsModels)

  return (
    <>
      <Card>
        <CardBody className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold">Language models</h2>
            <p className="text-xs text-[var(--text-muted)]">
              Three task classes, routed independently. Paying frontier prices to classify
              transcript relevance costs more than the rest of the pipeline and is no better
              at it.
            </p>
          </div>

          {TASK_CLASSES.map((task) => {
            const current = (settings[`routing.${task.key}`] ?? {}) as {
              provider?: string
              model?: string
            }
            const value = current.provider && current.model ? `${current.provider}|${current.model}` : ''
            return (
              <Field key={task.key} label={task.label} hint={task.hint}>
                <Select
                  value={value}
                  onValueChange={(next) => {
                    const [provider, model] = next.split('|')
                    onSave({ [`routing.${task.key}`]: { provider, model } })
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Not set" />
                  </SelectTrigger>
                  <SelectContent>
                    {llmOptions.length === 0 && (
                      <div className="px-2 py-3 text-xs text-[var(--text-muted)]">
                        Add a provider key first.
                      </div>
                    )}
                    {llmOptions.map((option) => (
                      <SelectItem
                        key={`${option.provider}|${option.model}`}
                        value={`${option.provider}|${option.model}`}
                      >
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )
          })}
        </CardBody>
      </Card>

      <Card>
        <CardBody className="space-y-4">
          <h2 className="text-sm font-semibold">Speech and embeddings</h2>

          <Field label="Transcription" hint="Needed for audio and video. Groq is the cheapest.">
            <Select
              value={
                settings['stt.provider'] && settings['stt.model']
                  ? `${settings['stt.provider']}|${settings['stt.model']}`
                  : ''
              }
              onValueChange={(next) => {
                const [provider, model] = next.split('|')
                onSave({ 'stt.provider': provider, 'stt.model': model })
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Not set" />
              </SelectTrigger>
              <SelectContent>
                {sttOptions.map((option) => (
                  <SelectItem
                    key={`${option.provider}|${option.model}`}
                    value={`${option.provider}|${option.model}`}
                  >
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label="Text to speech" hint="Only needed for podcasts.">
            <Select
              value={
                settings['tts.provider'] && settings['tts.model']
                  ? `${settings['tts.provider']}|${settings['tts.model']}`
                  : ''
              }
              onValueChange={(next) => {
                const [provider, model] = next.split('|')
                onSave({ 'tts.provider': provider, 'tts.model': model })
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Not set" />
              </SelectTrigger>
              <SelectContent>
                {ttsOptions.map((option) => (
                  <SelectItem
                    key={`${option.provider}|${option.model}`}
                    value={`${option.provider}|${option.model}`}
                  >
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field
            label="Embeddings"
            hint="Powers chat and semantic search. The default runs locally and needs no key."
          >
            <Select
              value={String(settings['embedding.provider'] ?? 'fastembed')}
              onValueChange={(next) => onSave({ 'embedding.provider': next })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="fastembed">fastembed (local, no key)</SelectItem>
                <SelectItem value="openai">OpenAI</SelectItem>
                <SelectItem value="ollama">Ollama (local)</SelectItem>
                <SelectItem value="mock">Mock (offline demo)</SelectItem>
              </SelectContent>
            </Select>
          </Field>
        </CardBody>
      </Card>
    </>
  )
}

function GenerationSection({
  settings, onSave,
}: {
  settings: Record<string, unknown>
  onSave: (values: Record<string, unknown>) => void
}) {
  const { theme, setTheme } = useTheme()
  const [threshold, setThreshold] = useState(Number(settings['generation.relevance_threshold'] ?? 0.3))
  const [maxCards, setMaxCards] = useState(Number(settings['generation.max_cards_per_source'] ?? 50))

  useEffect(() => {
    setThreshold(Number(settings['generation.relevance_threshold'] ?? 0.3))
    setMaxCards(Number(settings['generation.max_cards_per_source'] ?? 50))
  }, [settings])

  return (
    <>
      <Card>
        <CardBody className="space-y-4">
          <h2 className="text-sm font-semibold">Generation</h2>

          <Field label="Default note style">
            <Select
              value={String(settings['generation.note_style'] ?? 'detailed')}
              onValueChange={(value) => onSave({ 'generation.note_style': value })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="detailed">Detailed — prose and examples</SelectItem>
                <SelectItem value="concise">Concise — bullet-dominant</SelectItem>
                <SelectItem value="cornell">Cornell — cue, notes, summary</SelectItem>
                <SelectItem value="exam_prep">Exam prep — definitions first</SelectItem>
              </SelectContent>
            </Select>
          </Field>

          <Field
            label={`Relevance threshold — ${threshold.toFixed(2)}`}
            hint="Segments scoring below this are kept in the transcript but withheld from note generation. Lower it to keep tangents; raise it to be ruthless about admin and cross-talk."
          >
            <input
              type="range"
              min={0}
              max={0.8}
              step={0.05}
              value={threshold}
              onChange={(event) => setThreshold(Number(event.target.value))}
              onMouseUp={() => onSave({ 'generation.relevance_threshold': threshold })}
              onTouchEnd={() => onSave({ 'generation.relevance_threshold': threshold })}
              className="w-full accent-[var(--accent)]"
            />
          </Field>

          <Field label="Maximum flashcards per source">
            <Input
              type="number"
              min={5}
              max={300}
              value={maxCards}
              onChange={(event) => setMaxCards(Number(event.target.value))}
              onBlur={() => onSave({ 'generation.max_cards_per_source': maxCards })}
            />
          </Field>

          <label className="flex cursor-pointer items-center justify-between gap-3">
            <span>
              <span className="block text-[13px] font-medium">Speaker diarization</span>
              <span className="block text-xs text-[var(--text-muted)]">
                Label who is speaking. Only Deepgram supports this.
              </span>
            </span>
            <Switch
              checked={Boolean(settings['stt.diarize'])}
              onCheckedChange={(checked) => onSave({ 'stt.diarize': checked })}
            />
          </label>
        </CardBody>
      </Card>

      <Card>
        <CardBody className="space-y-4">
          <h2 className="text-sm font-semibold">Appearance</h2>
          <Field label="Theme">
            <div className="flex gap-2">
              {(['light', 'dark', 'system'] as const).map((option) => (
                <button
                  key={option}
                  onClick={() => setTheme(option)}
                  className={cn(
                    'flex flex-1 items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-sm capitalize transition-colors',
                    theme === option
                      ? 'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]'
                      : 'border-[var(--border)] hover:bg-[var(--bg-sunken)]',
                  )}
                >
                  {option === 'light' && <Sun className="h-3.5 w-3.5" />}
                  {option === 'dark' && <Moon className="h-3.5 w-3.5" />}
                  {option === 'system' && <Check className="h-3.5 w-3.5" />}
                  {option}
                </button>
              ))}
            </div>
          </Field>
        </CardBody>
      </Card>
    </>
  )
}

function UsageSection() {
  const { data } = useQuery({ queryKey: ['usage'], queryFn: () => api.usage() })
  if (!data) return <Skeleton className="h-64" />

  return (
    <div className="space-y-4">
      <Card>
        <CardBody>
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <p className="text-xs text-[var(--text-muted)]">Total spend</p>
              <p className="text-2xl font-semibold tabular-nums">{formatCost(data.total_usd)}</p>
            </div>
            <div>
              <p className="text-xs text-[var(--text-muted)]">API calls</p>
              <p className="text-2xl font-semibold tabular-nums">{data.calls}</p>
            </div>
            <div>
              <p className="text-xs text-[var(--text-muted)]">Tokens</p>
              <p className="text-2xl font-semibold tabular-nums">
                {((data.prompt_tokens + data.completion_tokens) / 1000).toFixed(1)}k
              </p>
            </div>
          </div>
          <p className="mt-3 border-t border-[var(--border)] pt-3 text-[11px] text-[var(--text-muted)]">
            Estimates from a bundled price table last reviewed {data.prices_last_reviewed}.
            Providers change prices, so treat this as a guide rather than an invoice.
          </p>
        </CardBody>
      </Card>

      {data.by_provider.length > 0 && (
        <Card>
          <div className="border-b border-[var(--border)] px-4 py-3">
            <h3 className="text-sm font-semibold">By provider</h3>
          </div>
          <ul className="divide-y divide-[var(--border)]">
            {data.by_provider.map((row) => (
              <li key={row.provider} className="flex items-center justify-between px-4 py-2 text-sm">
                <span>{row.provider}</span>
                <span className="tabular-nums text-[var(--text-muted)]">
                  {row.calls} calls · {formatCost(row.usd)}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {(data.recent?.length ?? 0) > 0 && (
        <Card>
          <div className="border-b border-[var(--border)] px-4 py-3">
            <h3 className="text-sm font-semibold">Recent calls</h3>
            <p className="text-xs text-[var(--text-muted)]">
              Every provider call is logged — this is what makes a quality regression debuggable.
            </p>
          </div>
          <div className="max-h-96 overflow-y-auto">
            <table className="w-full text-xs">
              <tbody className="divide-y divide-[var(--border)]">
                {data.recent?.map((call) => (
                  <tr key={call.id} className={call.ok ? '' : 'bg-red-500/5'}>
                    <td className="px-4 py-1.5">{call.purpose}</td>
                    <td className="px-2 py-1.5 text-[var(--text-muted)]">{call.model}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-[var(--text-muted)]">
                      {call.prompt_tokens + call.completion_tokens || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      {formatCost(call.cost_usd)}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-[var(--text-muted)]">
                      {call.latency_ms}ms
                    </td>
                    <td className="px-4 py-1.5 text-right text-[var(--text-muted)]">
                      {formatRelative(call.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
