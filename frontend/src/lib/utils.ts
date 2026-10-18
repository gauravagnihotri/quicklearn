import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '—'
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (hours) return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  return `${minutes}:${String(secs).padStart(2, '0')}`
}

export function formatClock(ms: number): string {
  return formatDuration(ms / 1000)
}

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const diff = Date.now() - then
  const minutes = Math.round(diff / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days}d ago`
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** Money that never rounds a real cost away to "$0.00". */
export function formatCost(usd: number, known = true): string {
  if (!known) return 'unknown'
  if (usd === 0) return '$0.00'
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value)
}

const KIND_LABEL: Record<string, string> = {
  audio: 'Audio', video: 'Video', pdf: 'PDF', docx: 'Word', pptx: 'Slides',
  youtube: 'YouTube', web: 'Article', text: 'Text', image: 'Image',
}
export const kindLabel = (kind: string) => KIND_LABEL[kind] ?? kind

const STAGE_LABEL: Record<string, string> = {
  ingest: 'Processing', normalize: 'Preparing media', transcribe: 'Extracting content',
  extract: 'Extracting content', segment: 'Segmenting', filter: 'Assessing relevance',
  generate_notes: 'Writing notes', generate_simplified_notes: 'Writing simplified notes',
  generate_concepts: 'Extracting concepts',
  generate_cards: 'Creating flashcards', generate_quiz: 'Writing quiz questions',
  generate_podcast: 'Generating podcast', index: 'Indexing',
}
export const jobLabel = (type: string) => STAGE_LABEL[type] ?? type

export function debounce<A extends unknown[]>(fn: (...args: A) => void, ms: number) {
  let handle: ReturnType<typeof setTimeout> | undefined
  const wrapped = (...args: A) => {
    if (handle) clearTimeout(handle)
    handle = setTimeout(() => fn(...args), ms)
  }
  wrapped.cancel = () => {
    if (handle) clearTimeout(handle)
  }
  wrapped.flush = (...args: A) => {
    if (handle) clearTimeout(handle)
    fn(...args)
  }
  return wrapped
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  // Revoke on the next tick; revoking immediately can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
export const modKey = isMac ? '⌘' : 'Ctrl'

/** Extract plain text from a TipTap node tree, for previews and word counts. */
export function nodeText(node: { text?: string; content?: unknown[] } | null | undefined): string {
  if (!node) return ''
  if (typeof node.text === 'string') return node.text
  if (!Array.isArray(node.content)) return ''
  return node.content.map((child) => nodeText(child as never)).join('')
}
