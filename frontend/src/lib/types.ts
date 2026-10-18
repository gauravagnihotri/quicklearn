// Shapes returned by the API. Hand-written rather than generated: the surface is small
// enough that a generator would be more machinery than it saves, and these double as the
// place to document what each field means to the UI.

export type SourceStatus = 'recording' | 'pending' | 'processing' | 'ready' | 'failed'
export type SourceKind =
  | 'audio' | 'video' | 'pdf' | 'docx' | 'pptx' | 'youtube' | 'web' | 'text' | 'image'
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type CardState = 'new' | 'learning' | 'review' | 'relearning'
export type QuizItemType = 'mcq' | 'cloze' | 'short' | 'free'
export type NoteStyle = 'concise' | 'detailed' | 'cornell' | 'exam_prep'

export interface Source {
  id: number
  title: string
  kind: SourceKind
  origin: string
  status: SourceStatus
  folder_id: number | null
  original_filename: string | null
  duration_sec: number | null
  page_count: number | null
  size_bytes: number | null
  has_media: boolean
  has_audio: boolean
  metadata: Record<string, unknown>
  error: string | null
  tags: string[]
  created_at: string
  updated_at: string
}

export interface Segment {
  id: number
  index: number
  text: string
  start_ms: number | null
  end_ms: number | null
  timestamp: string
  page_no: number | null
  bbox: number[] | null
  speaker: string | null
  relevance: number
  is_marked: boolean
  source_id: number
}

export interface NoteBlock {
  id: number
  index: number
  type: string
  text: string
  segment_ids: number[]
}

export interface Note {
  id: number
  source_id: number
  title: string
  doc: TipTapDoc
  outline: { level: number; text: string; index: number }[]
  style: string
  version: number
  is_dirty: boolean
  blocks?: NoteBlock[]
  created_at: string
  updated_at: string
}

export interface TipTapDoc {
  type: string
  content?: TipTapNode[]
}
export interface TipTapNode {
  type: string
  attrs?: Record<string, unknown>
  content?: TipTapNode[]
  text?: string
  marks?: { type: string; attrs?: Record<string, unknown> }[]
}

export interface SourceDetail {
  source: Source
  note: Note | null
  /** Local patch: the ADHD-friendly second note, written after ingest. */
  simplified_note: Note | null
  transcript: { language: string | null; provider: string | null; model: string | null } | null
  counts: { segments: number; concepts: number; cards: number; quiz_items: number }
  jobs: Job[]
}

export interface Job {
  id: number
  parent_id: number | null
  source_id: number | null
  type: string
  status: JobStatus
  progress: number
  stage: string
  error: string | null
  attempts: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  children?: Job[]
}

export interface Folder {
  id: number
  name: string
  parent_id: number | null
  color: string | null
  position: number
  source_count: number
  created_at: string
}

export interface Concept {
  id: number
  source_id: number
  name: string
  definition: string
  importance: number
  mastery: number
  segment_ids: number[]
}

export interface Card {
  id: number
  source_id: number | null
  concept_id: number | null
  type: string
  front: string
  back: string
  state: CardState
  due: string | null
  reps: number
  lapses: number
  stability: number | null
  difficulty: number | null
  suspended?: boolean
  segment_ids: number[]
  source_title?: string
  /** What each rating button would schedule — shown so the user can rate honestly. */
  intervals?: Record<string, string>
}

export interface DueCounts {
  new: number
  due: number
  total: number
}

export interface ReviewResult {
  card_id: number
  due: string
  interval: string
  scheduled_days: number
  state: CardState
  reps: number
  lapses: number
  mastery?: number
}

export interface QuizQuestion {
  done: false
  id: number
  source_id: number | null
  concept_id: number | null
  type: QuizItemType
  question: string
  options: string[]
  difficulty: number
  segment_ids: number[]
  concept: { id: number; name: string; mastery: number } | null
  served: number
  progress: MasterySummary
}

export interface QuizDone {
  done: true
  reason: string
}

export interface MasterySummary {
  concepts: number
  average_mastery: number
  mastered: number
  threshold: number
}

export interface AnswerResult {
  item_id: number
  score: number
  mastery: number
  is_correct?: boolean
  feedback?: string
  correct_answer?: string
  explanation?: string
  segment_ids?: number[]
}

export interface QuizResults {
  session_id: number
  mode: string
  status: string
  answered: number
  correct: number
  accuracy: number
  average_score: number
  items: {
    question: string
    response: string
    correct_answer: string
    is_correct: boolean
    score: number
    feedback: string
    explanation: string
    segment_ids: number[]
    source_id: number | null
  }[]
  weak_concepts: {
    id: number
    name: string
    definition: string
    mastery: number
    source_id: number
    source_title: string
    segment_ids: number[]
  }[]
  mastery: MasterySummary
}

export interface Chat {
  id: number
  scope_type: 'source' | 'folder' | 'global'
  scope_id: number | null
  title: string
  message_count: number
  created_at: string
}

export interface ChatMessage {
  id: number
  chat_id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  citations: number[]
  followups: string[]
  created_at: string
}

export interface Excerpt {
  id: number
  text: string
  source_id: number
  source_title: string
  timestamp: string
  page_no: number | null
  start_ms: number | null
  score: number
  matched_by: string[]
}

export interface Podcast {
  id: number
  title: string
  source_id: number | null
  source_ids: number[]
  script: { speaker: string; text: string }[]
  status: 'pending' | 'scripting' | 'synthesizing' | 'assembling' | 'ready' | 'failed'
  duration_sec: number | null
  target_minutes: number
  voice_config: Record<string, string>
  has_audio: boolean
  error: string | null
  created_at: string
}

export interface SearchHit {
  kind: string
  id: number
  source_id: number | null
  title: string
  snippet: string
  score: number
  match: 'keyword' | 'semantic'
}

export interface ProviderInfo {
  name: string
  label: string
  kinds: string[]
  requires_api_key: boolean
  is_local: boolean
  default_base_url: string
  has_key: boolean
  base_url: string
  configured: boolean
}

export interface ModelInfo {
  id: string
  name: string
  context_window: number
}

export interface SettingsPayload {
  settings: Record<string, unknown> & { _secrets_present?: Record<string, boolean> }
  providers: ProviderInfo[]
  defaults: Record<string, unknown>
  configured: { llm: boolean; stt: boolean; tts: boolean }
}

export interface Usage {
  total_usd: number
  calls: number
  prompt_tokens: number
  completion_tokens: number
  by_provider: { provider: string; usd: number; calls: number }[]
  prices_last_reviewed: string
  recent?: UsageCall[]
}

export interface UsageCall {
  id: number
  provider: string
  model: string
  purpose: string
  task_class: string
  prompt_tokens: number
  completion_tokens: number
  audio_seconds: number
  characters: number
  cost_usd: number
  latency_ms: number
  ok: boolean
  error: string | null
  created_at: string
}

export interface Dashboard {
  totals: { sources: number; notes: number; cards: number; concepts: number; segments: number }
  recent_sources: Source[]
  due: DueCounts
  stats: {
    total_reviews: number
    reviewed_today: number
    streak_days: number
    cards: number
    concepts: number
    can_optimize: boolean
    optimizer_available: boolean
    optimize_threshold: number
  }
  active_jobs: Job[]
  usage: Usage
  weak_concepts: { id: number; name: string; mastery: number; source_id: number; source_title: string }[]
  mastery: MasterySummary
  providers_configured: { llm: boolean; stt: boolean; tts: boolean }
  unfinished_recordings: number
}

export interface UnfinishedRecording {
  id: number
  title: string
  created_at: string
  size_bytes: number
  has_data: boolean
}

export interface JobEvent {
  type: string
  topic?: string
  job_id?: number
  source_id?: number
  progress?: number
  stage?: string
  error?: string
  status?: string
  jobs?: Job[]
  section?: string
  done?: number
  total?: number
  podcast_id?: number
}
