import { create } from 'zustand'
import type { Job, JobEvent } from './types'

// ---------------------------------------------------------------- theme

type Theme = 'light' | 'dark' | 'system'

interface ThemeState {
  theme: Theme
  setTheme: (theme: Theme) => void
}

function applyTheme(theme: Theme) {
  const dark =
    theme === 'dark' ||
    (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
}

export const useTheme = create<ThemeState>((set) => ({
  theme: (localStorage.getItem('quicklearn-theme') as Theme) || 'system',
  setTheme: (theme) => {
    localStorage.setItem('quicklearn-theme', theme)
    applyTheme(theme)
    set({ theme })
  },
}))

// Follow the OS when set to "system", so the app changes with it rather than at next reload.
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (useTheme.getState().theme === 'system') applyTheme('system')
})

// ---------------------------------------------------------------- audio player
//
// Player state is global rather than component-local because clicking a source anchor inside
// the note editor has to seek a player that lives in a different part of the tree.

interface PlayerState {
  sourceId: number | null
  currentMs: number
  durationMs: number
  playing: boolean
  /** Bumped to request a seek. The player component watches this rather than a plain value,
   *  so seeking to the position you are already at still works. */
  seekRequest: { ms: number; nonce: number } | null
  setSource: (sourceId: number | null) => void
  setTime: (ms: number) => void
  setDuration: (ms: number) => void
  setPlaying: (playing: boolean) => void
  seekTo: (ms: number) => void
}

export const usePlayer = create<PlayerState>((set) => ({
  sourceId: null,
  currentMs: 0,
  durationMs: 0,
  playing: false,
  seekRequest: null,
  setSource: (sourceId) => set({ sourceId, currentMs: 0, playing: false, seekRequest: null }),
  setTime: (currentMs) => set({ currentMs }),
  setDuration: (durationMs) => set({ durationMs }),
  setPlaying: (playing) => set({ playing }),
  seekTo: (ms) => set({ seekRequest: { ms, nonce: Date.now() + Math.random() } }),
}))

// ---------------------------------------------------------------- jobs
//
// Live job state, fed by the /ws/jobs socket. Kept in a store rather than React Query so
// progress ticks re-render only the job tray, not every screen that happens to be mounted.

interface JobState {
  jobs: Record<number, Job>
  connected: boolean
  /** Latest streamed note section, so the source page can show notes building live. */
  lastSection: { sourceId: number; section: string; done: number; total: number } | null
  applyEvent: (event: JobEvent) => void
  setConnected: (connected: boolean) => void
  replaceAll: (jobs: Job[]) => void
  clearFinished: () => void
}

export const useJobs = create<JobState>((set) => ({
  jobs: {},
  connected: false,
  lastSection: null,
  setConnected: (connected) => set({ connected }),
  replaceAll: (jobs) =>
    set({ jobs: Object.fromEntries(jobs.map((job) => [job.id, job])) }),
  clearFinished: () =>
    set((state) => ({
      jobs: Object.fromEntries(
        Object.entries(state.jobs).filter(
          ([, job]) => job.status === 'queued' || job.status === 'running',
        ),
      ),
    })),
  applyEvent: (event) =>
    set((state) => {
      if (event.type === 'snapshot' && event.jobs) {
        return { jobs: Object.fromEntries(event.jobs.map((job) => [job.id, job])) }
      }
      if (event.type === 'note.section' && event.source_id) {
        return {
          lastSection: {
            sourceId: event.source_id,
            section: event.section ?? '',
            done: event.done ?? 0,
            total: event.total ?? 0,
          },
        }
      }
      if (!event.job_id) return state

      const existing = state.jobs[event.job_id]
      const base: Job =
        existing ??
        ({
          id: event.job_id,
          parent_id: null,
          source_id: event.source_id ?? null,
          type: 'ingest',
          status: 'queued',
          progress: 0,
          stage: '',
          error: null,
          attempts: 0,
          created_at: new Date().toISOString(),
          started_at: null,
          finished_at: null,
        } as Job)

      const next: Job = { ...base }
      if (event.progress !== undefined) next.progress = event.progress
      if (event.stage) next.stage = event.stage
      if (event.source_id) next.source_id = event.source_id
      if (event.type === 'job.succeeded') {
        next.status = 'succeeded'
        next.progress = 100
      } else if (event.type === 'job.failed') {
        next.status = 'failed'
        next.error = event.error ?? null
      } else if (event.type === 'job.cancelled') {
        next.status = 'cancelled'
      } else if (event.type === 'job.progress') {
        next.status = 'running'
      }

      return { jobs: { ...state.jobs, [event.job_id]: next } }
    }),
}))

// ---------------------------------------------------------------- command palette

interface UiState {
  paletteOpen: boolean
  setPaletteOpen: (open: boolean) => void
  sidebarOpen: boolean
  setSidebarOpen: (open: boolean) => void
}

export const useUi = create<UiState>((set) => ({
  paletteOpen: false,
  setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
  // Collapsed by default on narrow screens; the layout opens it on desktop at mount.
  sidebarOpen: typeof window !== 'undefined' ? window.innerWidth >= 1024 : true,
  setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
}))
