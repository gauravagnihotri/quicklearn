import { Link, Outlet, useRouterState } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen, Brain, LayoutDashboard, Menu, Mic, MessageSquare, Headphones,
  Search, Settings as SettingsIcon, Zap, ListChecks,
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
  const { sidebarOpen, setSidebarOpen, setPaletteOpen } = useUi()
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
        )}
      >
        <div className="flex h-14 items-center gap-2 px-4">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--accent)] text-white">
              <Zap className="h-4 w-4" />
            </span>
            quicklearn
          </Link>
        </div>

        <div className="px-3 pb-2">
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            className="flex w-full items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-1.5 text-[13px] text-[var(--text-muted)] transition hover:border-[var(--accent)]/40"
          >
            <Search className="h-3.5 w-3.5" />
            <span className="flex-1 text-left">Search…</span>
            <Kbd>{modKey}K</Kbd>
          </button>
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-2">
          {NAV.map((item) => {
            const active = item.exact ? pathname === item.to : pathname.startsWith(item.to)
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors',
                  active
                    ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                    : 'text-[var(--text-muted)] hover:bg-[var(--bg-sunken)] hover:text-[var(--text)]',
                )}
              >
                <item.icon className="h-4 w-4 shrink-0" />
                <span className="flex-1">{item.label}</span>
                {item.badge === 'due' && dueTotal > 0 && (
                  <Badge tone="accent">{dueTotal > 999 ? '999+' : dueTotal}</Badge>
                )}
              </Link>
            )
          })}
        </nav>

        <div className="border-t border-[var(--border)] p-3">
          <JobTray />
          <Link
            to="/settings"
            className={cn(
              'mt-2 flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors',
              pathname.startsWith('/settings')
                ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                : 'text-[var(--text-muted)] hover:bg-[var(--bg-sunken)] hover:text-[var(--text)]',
            )}
          >
            <SettingsIcon className="h-4 w-4" />
            Settings
          </Link>
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
