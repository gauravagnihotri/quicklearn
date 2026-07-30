import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { AppShell } from './components/layout/AppShell'
import { DashboardPage } from './routes/Dashboard'
import { LibraryPage } from './routes/Library'
import { SourcePage } from './routes/Source'
import { ReviewPage } from './routes/Review'
import { QuizPage } from './routes/Quiz'
import { ChatPage } from './routes/Chat'
import { PodcastsPage } from './routes/Podcasts'
import { RecordPage } from './routes/Record'
import { SettingsPage } from './routes/Settings'
import { NotFound } from './routes/NotFound'

// Code-based routing rather than the file-based plugin: it removes a codegen step and a
// generated file that can drift, with no user-visible difference.

const rootRoute = createRootRoute({
  component: AppShell,
  notFoundComponent: NotFound,
})

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: DashboardPage })

// Search params are declared with *optional* properties. Returning `{ folder: undefined }`
// from an un-annotated validator makes TanStack treat every key as required, which forces
// every `Link` and `navigate` in the app to spell out search params it does not care about.

interface LibrarySearch {
  folder?: number
  tag?: string
}
const libraryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/library',
  component: LibraryPage,
  validateSearch: (search: Record<string, unknown>): LibrarySearch => ({
    folder: search.folder ? Number(search.folder) : undefined,
    tag: typeof search.tag === 'string' ? search.tag : undefined,
  }),
})

interface SourceSearch {
  tab?: string
  seg?: number
  print?: boolean
}
const sourceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/library/$sourceId',
  component: SourcePage,
  validateSearch: (search: Record<string, unknown>): SourceSearch => ({
    tab: typeof search.tab === 'string' ? search.tab : undefined,
    seg: search.seg ? Number(search.seg) : undefined,
    print: search.print === true || search.print === 'true' ? true : undefined,
  }),
})

interface ReviewSearch {
  folder?: number
  source?: number
}
const reviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/review',
  component: ReviewPage,
  validateSearch: (search: Record<string, unknown>): ReviewSearch => ({
    folder: search.folder ? Number(search.folder) : undefined,
    source: search.source ? Number(search.source) : undefined,
  }),
})

interface QuizSearch {
  source?: number
  folder?: number
  mode?: string
}
const quizRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/quiz',
  component: QuizPage,
  validateSearch: (search: Record<string, unknown>): QuizSearch => ({
    source: search.source ? Number(search.source) : undefined,
    folder: search.folder ? Number(search.folder) : undefined,
    mode: typeof search.mode === 'string' ? search.mode : undefined,
  }),
})

interface ChatSearch {
  id?: number
  source?: number
}
const chatRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/chat',
  component: ChatPage,
  validateSearch: (search: Record<string, unknown>): ChatSearch => ({
    id: search.id ? Number(search.id) : undefined,
    source: search.source ? Number(search.source) : undefined,
  }),
})

const podcastsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/podcasts', component: PodcastsPage })
const recordRoute = createRoute({ getParentRoute: () => rootRoute, path: '/record', component: RecordPage })
const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/settings', component: SettingsPage })

const routeTree = rootRoute.addChildren([
  indexRoute,
  libraryRoute,
  sourceRoute,
  reviewRoute,
  quizRoute,
  chatRoute,
  podcastsRoute,
  recordRoute,
  settingsRoute,
])

export const router = createRouter({
  routeTree,
  defaultPreload: 'intent',
  defaultPreloadStaleTime: 0,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
