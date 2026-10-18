import { Link, Outlet, useRouterState } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen, Brain, LayoutDashboard, Loader2, Menu, Mic, MessageSquare, Headphones,
  PanelLeftClose, PanelLeftOpen, Search, Settings as SettingsIcon, Zap, ListChecks,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { useJobs, useUi } from '../../lib/store'
import { jobSocket } from '../../lib/ws'
import { cn, modKey } from '../../lib/utils'
import { Badge, Button, Kbd, Tooltip } from '../ui'
import { CommandPalette } from '../CommandPalette'
import { JobTray } from './JobTray'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, exact: true },
  { to: '/library', label: 'Library', icon: BookOpen },
  { to: '/review', label: 'Review', icon: Brain, badge: 'due' as const },
  { to: '/quiz', label: 'Quiz', icon: ListChecks },
  { to: '/chat', label: 'Chat', icon: MessageSquare },
  { to: '/podcasts', label: 'Podcasts', icon: Headphones },
  { to: '/record', label: 'Record', icon: Mic },
]

export function AppShell() {
  const { sidebarOpen, setSidebarOpen, setPaletteOpen, navCollapsed, setNavCollapsed } = useUi()
  // The rail only applies on desktop; the mobile drawer always shows labels.
  const onRail = navCollapsed ? 'lg:hidden' : ''
  const busyJobs = useJobs(
    (state) =>
      Object.values(state.jobs).filter((job) => job.status === 'running' || job.status === 'queued')
        .length,
  )
  const pathname = useRouterState({ select: (s) => s.location.pathname })

  // One socket for the whole app; see lib/ws.ts for why it is not a hook.
  useEffect(() => {
    jobSocket.connect()
    return () => jobSocket.disconnect()
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setPaletteOpen])

  // Close the drawer after navigating on mobile, or it covers the page you just opened.
  useEffect(() => {
    if (window.innerWidth < 1024) setSidebarOpen(false)
  }, [pathname, setSidebarOpen])

  const { data: counts } = useQuery({
    queryKey: ['card-counts'],
    queryFn: () => api.cardCounts(),
    refetchInterval: 60_000,
  })
  const dueTotal = (counts?.due ?? 0) + (counts?.new ?? 0)

  return (
    <div className="flex h-full overflow-hidden">
      {sidebarOpen && (
        <button
          type="button"
          aria-label="Close menu"
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-[var(--border)]',
          'bg-[var(--bg-raised)] transition-transform lg:static lg:translate-x-0',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full',
          navCollapsed && 'lg:w-14',
        )}
      >
        <div className={cn('flex h-14 items-center gap-2 px-4', navCollapsed && 'lg:justify-center lg:px-0')}>
          <Link to="/" className={cn('flex flex-1 items-center gap-2 font-semibold tracking-tight', onRail)}>
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--accent)] text-white">
              <Zap className="h-4 w-4" />
            </span>
            quicklearn
          </Link>
          <Tooltip label={navCollapsed ? 'Expand sidebar' : 'Collapse sidebar'} side="right">
            <Button
              variant="ghost"
              size="icon-sm"
              className="hidden lg:inline-flex"
              aria-label={navCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              onClick={() => setNavCollapsed(!navCollapsed)}
            >
              {navCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </Button>
          </Tooltip>
        </div>

        <div className={cn('px-3 pb-2', navCollapsed && 'lg:px-2')}>
          <Tooltip label={navCollapsed ? `Search (${modKey}K)` : null} side="right">
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className={cn(
                'flex w-full items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-1.5 text-[13px] text-[var(--text-muted)] transition hover:border-[var(--accent)]/40',
                navCollapsed && 'lg:justify-center lg:px-0 lg:py-2',
              )}
            >
              <Search className="h-3.5 w-3.5 shrink-0" />
              <span className={cn('flex-1 text-left', onRail)}>Search…</span>
              <span className={onRail}>
                <Kbd>{modKey}K</Kbd>
              </span>
            </button>
          </Tooltip>
        </div>

        <nav className={cn('flex-1 space-y-0.5 overflow-y-auto px-3 py-2', navCollapsed && 'lg:px-2')}>
          {NAV.map((item) => {
            const active = item.exact ? pathname === item.to : pathname.startsWith(item.to)
            const due = item.badge === 'due' && dueTotal > 0
            return (
              <Tooltip key={item.to} label={navCollapsed ? item.label : null} side="right">
                <Link
                  to={item.to}
                  className={cn(
                    'relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors',
                    navCollapsed && 'lg:justify-center lg:px-0',
                    active
                      ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                      : 'text-[var(--text-muted)] hover:bg-[var(--bg-sunken)] hover:text-[var(--text)]',
                  )}
                >
                  <item.icon className="h-4 w-4 shrink-0" />
                  <span className={cn('flex-1', onRail)}>{item.label}</span>
                  {due && (
                    <Badge tone="accent" className={onRail}>
                      {dueTotal > 999 ? '999+' : dueTotal}
                    </Badge>
                  )}
                  {due && navCollapsed && (
                    <span className="absolute right-1.5 top-1.5 hidden h-1.5 w-1.5 rounded-full bg-[var(--accent)] lg:block" />
                  )}
                </Link>
              </Tooltip>
            )
          })}
        </nav>

        <div className={cn('border-t border-[var(--border)] p-3', navCollapsed && 'lg:p-2')}>
          <div className={onRail}>
            <JobTray />
          </div>
          {navCollapsed && busyJobs > 0 && (
            <Tooltip label={`${busyJobs} job${busyJobs === 1 ? '' : 's'} running`} side="right">
              <button
                type="button"
                aria-label="Show running jobs"
                onClick={() => setNavCollapsed(false)}
                className="hidden w-full place-items-center rounded-lg py-2 hover:bg-[var(--bg-sunken)] lg:grid"
              >
                <Loader2 className="h-4 w-4 animate-spin text-[var(--accent)]" />
              </button>
            </Tooltip>
          )}
          <Tooltip label={navCollapsed ? 'Settings' : null} side="right">
            <Link
              to="/settings"
              className={cn(
                'mt-2 flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors',
                navCollapsed && 'lg:justify-center lg:px-0',
                pathname.startsWith('/settings')
                  ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                  : 'text-[var(--text-muted)] hover:bg-[var(--bg-sunken)] hover:text-[var(--text)]',
              )}
            >
              <SettingsIcon className="h-4 w-4 shrink-0" />
              <span className={onRail}>Settings</span>
            </Link>
          </Tooltip>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-[var(--border)] px-3 lg:hidden">
          <Button variant="ghost" size="icon" onClick={() => setSidebarOpen(!sidebarOpen)}>
            <Menu className="h-4 w-4" />
          </Button>
          <span className="font-semibold">quicklearn</span>
          <div className="flex-1" />
          <Tooltip label="Search">
            <Button variant="ghost" size="icon" onClick={() => setPaletteOpen(true)}>
              <Search className="h-4 w-4" />
            </Button>
          </Tooltip>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>

      <CommandPalette />
      <ConnectionBanner />
    </div>
  )
}

/** Shown only when the socket is down, so a stopped server is obvious rather than
 *  presenting as an app that has mysteriously stopped updating. */
function ConnectionBanner() {
  const connected = useJobs((state) => state.connected)
  const [visible, setVisible] = useDelayed(!connected, 3000)
  if (!visible) return null
  return (
    <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-700 shadow-lg dark:text-amber-300">
      Reconnecting to the quicklearn server…
      <button className="ml-2 underline" onClick={() => setVisible(false)}>
        dismiss
      </button>
    </div>
  )
}

/** True only after `value` has stayed true for `delay` ms — avoids flashing a warning
 *  during the normal one-second reconnect after a hot reload. */
function useDelayed(value: boolean, delay: number): [boolean, (v: boolean) => void] {
  const [state, setState] = useState(false)
  useEffect(() => {
    if (!value) {
      setState(false)
      return
    }
    const handle = setTimeout(() => setState(true), delay)
    return () => clearTimeout(handle)
  }, [value, delay])
  return [state, setState]
}
