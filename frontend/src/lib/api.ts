import type {
  AnswerResult, Card, Chat, ChatMessage, Concept, Dashboard, DueCounts, Excerpt, Folder,
  Job, Note, Podcast, QuizDone, QuizQuestion, QuizResults, ReviewResult, SearchHit,
  Segment, SettingsPayload, Source, SourceDetail, UnfinishedRecording, Usage,
} from './types'

const BASE = '/api/v1'

/** An API error carrying the server's actionable message rather than a status code. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly kind: string = 'Error',
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers:
        init?.body instanceof FormData || init?.body instanceof Blob
          ? init?.headers
          : { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new ApiError(
      'Could not reach the quicklearn server. Is it still running?',
      0,
      'NetworkError',
    )
  }

  if (!response.ok) {
    // The backend's error taxonomy puts a human-readable message in `message`; surface that
    // rather than "Request failed with status 502".
    let message = `Request failed (${response.status})`
    let kind = 'Error'
    try {
      const body = await response.json()
      if (typeof body?.message === 'string') message = body.message
      if (typeof body?.error === 'string') kind = body.error
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, response.status, kind)
  }

  if (response.status === 204) return undefined as T
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) return (await response.text()) as T
  return (await response.json()) as T
}

const get = <T>(path: string) => request<T>(path)
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
const del = <T>(path: string) => request<T>(path, { method: 'DELETE' })

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const api = {
  // ---------------------------------------------------------------- sources
  listSources: (params: { folder_id?: number; status?: string; tag?: string; q?: string } = {}) =>
    get<{ sources: Source[]; total: number }>(`/sources${qs(params)}`),
  getSource: (id: number) => get<SourceDetail>(`/sources/${id}`),
  updateSource: (id: number, body: { title?: string; folder_id?: number | null; tags?: string[] }) =>
    patch<Source>(`/sources/${id}`, body),
  deleteSource: (id: number) => del<{ deleted: boolean }>(`/sources/${id}`),
  getSegments: (id: number) => get<{ segments: Segment[] }>(`/sources/${id}/segments`),
  getTranscript: (id: number) =>
    get<{ segments: Segment[]; text: string }>(`/sources/${id}/transcript`),
  getConcepts: (id: number) => get<{ concepts: Concept[] }>(`/sources/${id}/concepts`),
  addUrl: (url: string, folder_id?: number | null) =>
    post<{ source_id: number; job_id: number; kind: string }>('/sources/url', { url, folder_id }),
  addText: (title: string, content: string, folder_id?: number | null) =>
    post<{ source_id: number; job_id: number }>('/sources/text', { title, content, folder_id }),
  regenerate: (
    id: number,
    body: { target: 'notes' | 'concepts' | 'cards' | 'quiz' | 'all'; style?: string; count?: number },
  ) => post<{ job_ids: number[]; note_was_edited: boolean }>(`/sources/${id}/regenerate`, body),
  reprocess: (id: number) => post<{ job_id: number }>(`/sources/${id}/reprocess`),
  mediaUrl: (id: number) => `${BASE}/sources/${id}/media`,
  audioUrl: (id: number) => `${BASE}/sources/${id}/audio`,

  /** Upload with progress. Uses XHR because fetch cannot report upload progress. */
  upload: (
    file: File,
    opts: { folderId?: number | null; onProgress?: (percent: number) => void } = {},
  ) =>
    new Promise<{ source_id: number; job_id: number }>((resolve, reject) => {
      const form = new FormData()
      form.append('file', file)
      if (opts.folderId != null) form.append('folder_id', String(opts.folderId))

      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${BASE}/sources/upload`)
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) opts.onProgress?.((event.loaded / event.total) * 100)
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText))
        } else {
          let message = `Upload failed (${xhr.status})`
          try {
            message = JSON.parse(xhr.responseText).message ?? message
          } catch { /* non-JSON */ }
          reject(new ApiError(message, xhr.status))
        }
      }
      xhr.onerror = () => reject(new ApiError('Upload failed — connection lost.', 0))
      xhr.send(form)
    }),

  // ---------------------------------------------------------------- recording
  startRecording: (title: string, folder_id?: number | null) =>
    post<{ source_id: number; upload_token: string; stt_configured: boolean; warning: string | null }>(
      '/sources/record/start',
      { title, folder_id },
    ),
  sendRecordingChunk: async (token: string, blob: Blob) => {
    await fetch(`${BASE}/sources/record/chunk?token=${encodeURIComponent(token)}`, {
      method: 'POST',
      body: blob,
      headers: { 'Content-Type': 'application/octet-stream' },
    })
  },
  addMarker: (sourceId: number, position_ms: number) =>
    post<{ markers: number[] }>(`/sources/${sourceId}/marker`, { position_ms }),
  stopRecording: (token: string) =>
    post<{ source_id: number; job_id: number }>('/sources/record/stop', { token }),
  unfinishedRecordings: () =>
    get<{ recordings: UnfinishedRecording[] }>('/sources/recordings/unfinished'),
  processUnfinished: (id: number) => post<{ job_id: number }>(`/sources/${id}/process-unfinished`),
  discardUnfinished: (id: number) => del<{ deleted: boolean }>(`/sources/recordings/unfinished/${id}`),

  // ---------------------------------------------------------------- notes
  getNote: (id: number) => get<Note>(`/notes/${id}`),
  saveNote: (id: number, doc: unknown) => patch<Note>(`/notes/${id}`, { doc }),
  renameNote: (id: number, title: string) => patch<Note>(`/notes/${id}`, { title }),
  noteSegments: (id: number) => get<{ segments: Segment[] }>(`/notes/${id}/segments`),
  aiAction: (id: number, action: string, selection: string, context = '') =>
    post<{ action: string; result: string }>(`/notes/${id}/ai-action`, { action, selection, context }),
  makeCardsFromSelection: (id: number, selection: string) =>
    post<{ created: number; card_ids: number[] }>(`/notes/${id}/make-cards`, {
      action: 'key_points',
      selection,
    }),
  exportUrl: (id: number, format: string) => `${BASE}/notes/${id}/export?format=${format}`,
  blockSegments: (blockId: number) => get<{ segments: Segment[] }>(`/blocks/${blockId}/segments`),

  // ---------------------------------------------------------------- cards
  dueCards: (params: { folder_id?: number; source_id?: number; limit?: number } = {}) =>
    get<{ cards: Card[]; counts: DueCounts }>(`/cards/due${qs(params)}`),
  cardCounts: (folder_id?: number) => get<DueCounts>(`/cards/counts${qs({ folder_id })}`),
  reviewCard: (id: number, rating: number, duration_ms?: number) =>
    post<ReviewResult>(`/cards/${id}/review`, { rating, duration_ms }),
  undoReview: (id: number) => post<{ undone: boolean }>(`/cards/${id}/undo`),
  listCards: (source_id?: number) => get<{ cards: Card[] }>(`/cards${qs({ source_id })}`),
  editCard: (id: number, body: { front?: string; back?: string; suspended?: boolean }) =>
    patch<Card>(`/cards/${id}`, body),
  deleteCard: (id: number) => del<{ deleted: boolean }>(`/cards/${id}`),
  forecast: (days = 30, folder_id?: number) =>
    get<{ forecast: { date: string; count: number; offset: number }[] }>(
      `/cards/forecast${qs({ days, folder_id })}`,
    ),
  cardStats: () => get<Dashboard['stats']>('/cards/stats'),
  optimizeFsrs: () => post<{ parameters: number[]; count: number }>('/cards/optimize-fsrs'),
  importCards: async (file: File, folderId?: number | null) => {
    const form = new FormData()
    form.append('file', file)
    if (folderId != null) form.append('folder_id', String(folderId))
    return request<{ imported: number; skipped: number; source_id: number }>('/cards/import', {
      method: 'POST',
      body: form,
    })
  },
  exportAnki: async (body: { source_ids?: number[]; folder_id?: number | null; deck_name?: string }) => {
    const response = await fetch(`${BASE}/cards/export/anki`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      let message = 'Export failed.'
      try {
        message = (await response.json()).message ?? message
      } catch { /* non-JSON */ }
      throw new ApiError(message, response.status)
    }
    return { blob: await response.blob(), count: response.headers.get('X-Card-Count') }
  },

  // ---------------------------------------------------------------- quiz
  createQuizSession: (body: {
    mode: 'practice' | 'test' | 'cumulative'
    source_ids?: number[]
    folder_id?: number | null
    length?: number | null
  }) => post<{ session_id: number; mode: string; length: number | null; concepts: number }>(
    '/quiz/session',
    body,
  ),
  nextQuestion: (sessionId: number) =>
    get<QuizQuestion | QuizDone>(`/quiz/session/${sessionId}/next`),
  answerQuestion: (sessionId: number, item_id: number, response: string) =>
    post<AnswerResult>(`/quiz/session/${sessionId}/answer`, { item_id, response }),
  quizResults: (sessionId: number) => get<QuizResults>(`/quiz/session/${sessionId}/results`),
  resetMastery: (source_id: number) => post<{ reset: number }>(`/quiz/reset-mastery${qs({ source_id })}`),

  // ---------------------------------------------------------------- chat
  listChats: () => get<{ chats: Chat[] }>('/chat'),
  createChat: (scope_type: 'source' | 'folder' | 'global', scope_id?: number | null, title = '') =>
    post<Chat>('/chat', { scope_type, scope_id, title }),
  chatMessages: (id: number) => get<{ chat: Chat; messages: ChatMessage[] }>(`/chat/${id}/messages`),
  sendChatMessage: (id: number, content: string) =>
    post<ChatMessage & { excerpts: Excerpt[] }>(`/chat/${id}/message`, { content }),
  deleteChat: (id: number) => del<{ deleted: boolean }>(`/chat/${id}`),
  resolveCitations: (ids: number[]) =>
    get<{ segments: Segment[] }>(`/chat/citations${qs({ ids: ids.join(',') })}`),

  // ---------------------------------------------------------------- podcasts
  listPodcasts: () => get<{ podcasts: Podcast[] }>('/podcasts'),
  getPodcast: (id: number) => get<Podcast>(`/podcasts/${id}`),
  createPodcast: (body: { source_ids: number[]; length: number; title?: string; voice_a?: string; voice_b?: string }) =>
    post<{ podcast_id: number; job_id: number }>('/podcasts', body),
  podcastVoices: () =>
    get<{ provider?: string; voices: { id: string; name: string; description: string }[]; error?: string }>(
      '/podcasts/voices',
    ),
  updatePodcastScript: (id: number, script: { speaker: string; text: string }[]) =>
    patch<Podcast>(`/podcasts/${id}/script`, { script }),
  regeneratePodcast: (id: number) => post<{ job_id: number }>(`/podcasts/${id}/regenerate`),
  deletePodcast: (id: number) => del<{ deleted: boolean }>(`/podcasts/${id}`),
  podcastAudioUrl: (id: number) => `${BASE}/podcasts/${id}/audio`,
  feedUrl: () => `${BASE}/podcasts/feed.xml`,

  // ---------------------------------------------------------------- library
  listFolders: () => get<{ folders: Folder[]; unfiled_count: number }>('/folders'),
  createFolder: (name: string, parent_id?: number | null, color?: string) =>
    post<Folder>('/folders', { name, parent_id, color }),
  updateFolder: (id: number, body: { name?: string; parent_id?: number | null; color?: string }) =>
    patch<Folder>(`/folders/${id}`, body),
  deleteFolder: (id: number, deleteSources = false) =>
    del<{ deleted: boolean }>(`/folders/${id}${qs({ delete_sources: deleteSources })}`),
  listTags: () => get<{ tags: { id: number; name: string; color: string | null }[] }>('/tags'),
  search: (q: string, semantic = false) =>
    get<{ query: string; results: SearchHit[]; semantic: boolean }>(`/search${qs({ q, semantic })}`),
  dashboard: () => get<Dashboard>('/dashboard'),

  // ---------------------------------------------------------------- system
  jobs: (activeOnly = false) => get<{ jobs: Job[] }>(`/jobs${qs({ active_only: activeOnly })}`),
  job: (id: number) => get<Job>(`/jobs/${id}`),
  cancelJob: (id: number) => post<{ cancelled: boolean }>(`/jobs/${id}/cancel`),
  retryJob: (id: number) => post<{ retried: boolean }>(`/jobs/${id}/retry`),
  settings: () => get<SettingsPayload>('/settings'),
  saveSettings: (values: Record<string, unknown>) =>
    patch<SettingsPayload>('/settings', { values }),
  testProvider: (body: { provider: string; kind: string; api_key?: string; base_url?: string }) =>
    post<{ ok: boolean; message: string; models: { id: string; name: string; context_window: number }[] }>(
      '/settings/test-provider',
      body,
    ),
  models: (kind: string, provider?: string) =>
    get<{ kind: string; providers: Record<string, { ok: boolean; message?: string; models: { id: string; name: string }[] }> }>(
      `/settings/models${qs({ kind, provider })}`,
    ),
  usage: () => get<Usage>('/usage'),
  estimate: (body: { text?: string; tokens?: number; audio_seconds?: number }) =>
    post<{ estimated_usd: number; known: boolean; breakdown: Record<string, { usd: number; known: boolean }> }>(
      '/usage/estimate',
      body,
    ),
  health: () => get<Record<string, unknown>>('/health'),
}
