import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import {
  ChevronRight, Folder as FolderIcon, FolderPlus, Inbox, MoreHorizontal, Plus, Search,
  Trash2, Upload,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import type { Folder, Source } from '../lib/types'
import { cn, formatDuration, formatRelative, kindLabel } from '../lib/utils'
import { AddSourceDialog } from '../components/AddSourceDialog'
import {
  Badge, Button, Card, Dialog, DialogContent, DialogTitle, EmptyState, Field, Input, Skeleton,
} from '../components/ui'
import { StatusBadge } from './Dashboard'

export function LibraryPage() {
  const search = useSearch({ from: '/library' })
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [newFolderOpen, setNewFolderOpen] = useState(false)
  const [folderName, setFolderName] = useState('')

  const { data: folderData } = useQuery({ queryKey: ['folders'], queryFn: () => api.listFolders() })
  const { data, isLoading } = useQuery({
    queryKey: ['sources', search.folder, search.tag, query],
    queryFn: () => api.listSources({ folder_id: search.folder, tag: search.tag, q: query || undefined }),
    refetchInterval: 15_000,
  })

  const createFolder = useMutation({
    mutationFn: () => api.createFolder(folderName.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['folders'] })
      setNewFolderOpen(false)
      setFolderName('')
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed.'),
  })

  const deleteSource = useMutation({
    mutationFn: (id: number) => api.deleteSource(id),
    onSuccess: () => {
      queryClient.invalidateQueries()
      toast.success('Source deleted.')
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed.'),
  })

  const tree = useMemo(() => buildTree(folderData?.folders ?? []), [folderData])
  const sources = data?.sources ?? []
  const activeFolder = folderData?.folders.find((f) => f.id === search.folder)

  return (
    <div className="mx-auto flex h-full max-w-7xl gap-5 p-4 sm:p-6">
      <aside className="hidden w-52 shrink-0 md:block">
        <div className="mb-2 flex items-center justify-between px-1">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            Folders
          </span>
          <button
            onClick={() => setNewFolderOpen(true)}
            className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--bg-sunken)] hover:text-[var(--text)]"
            aria-label="New folder"
          >
            <FolderPlus className="h-3.5 w-3.5" />
          </button>
        </div>

        <nav className="space-y-0.5">
          <FolderRow
            label="All sources"
            icon={Inbox}
            count={data?.total ?? 0}
            active={search.folder === undefined}
            onClick={() => navigate({ to: '/library', search: {} })}
          />
          {tree.map((node) => (
            <FolderNode
              key={node.folder.id}
              node={node}
              depth={0}
              activeId={search.folder}
              onSelect={(id) => navigate({ to: '/library', search: { folder: id } })}
            />
          ))}
        </nav>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="mb-4 flex flex-wrap items-center gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">
              {activeFolder ? activeFolder.name : 'Library'}
            </h1>
            <p className="text-xs text-[var(--text-muted)]">
              {data?.total ?? 0} source{(data?.total ?? 0) === 1 ? '' : 's'}
            </p>
          </div>
          <div className="flex-1" />
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--text-muted)]" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter by title…"
              className="w-52 pl-8"
            />
          </div>
          <AddSourceDialog
            defaultFolderId={search.folder}
            trigger={
              <Button variant="primary">
                <Plus className="h-4 w-4" /> Add
              </Button>
            }
          />
        </header>

        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-16" />
            ))}
          </div>
        ) : sources.length === 0 ? (
          <Card>
            <EmptyState
              icon={Upload}
              title={query ? `Nothing matches “${query}”` : 'This folder is empty'}
              description={
                query
                  ? 'Try a different search, or use Cmd+K to search inside your notes and transcripts.'
                  : 'Add a lecture, paper, or recording to get started.'
              }
              action={
                !query && <AddSourceDialog trigger={<Button variant="primary">Add source</Button>} />
              }
            />
          </Card>
        ) : (
          <div className="space-y-2">
            {sources.map((source) => (
              <SourceRow
                key={source.id}
                source={source}
                onDelete={() => {
                  if (
                    confirm(
                      `Delete “${source.title}” and everything generated from it? This cannot be undone.`,
                    )
                  ) {
                    deleteSource.mutate(source.id)
                  }
                }}
              />
            ))}
          </div>
        )}
      </div>

      <Dialog open={newFolderOpen} onOpenChange={setNewFolderOpen}>
        <DialogContent className="max-w-sm">
          <DialogTitle>New folder</DialogTitle>
          <Field label="Name">
            <Input
              autoFocus
              value={folderName}
              onChange={(event) => setFolderName(event.target.value)}
              onKeyDown={(event) =>
                event.key === 'Enter' && folderName.trim() && createFolder.mutate()
              }
              placeholder="Semester 1"
            />
          </Field>
          <div className="mt-4 flex justify-end gap-2">
            <Button onClick={() => setNewFolderOpen(false)}>Cancel</Button>
            <Button
              variant="primary"
              disabled={!folderName.trim()}
              loading={createFolder.isPending}
              onClick={() => createFolder.mutate()}
            >
              Create
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function SourceRow({ source, onDelete }: { source: Source; onDelete: () => void }) {
  const [menuOpen, setMenuOpen] = useState(false)
  return (
    <Card className="group relative transition-colors hover:border-[var(--accent)]/40">
      <Link
        to="/library/$sourceId"
        params={{ sourceId: String(source.id) }}
        className="flex items-center gap-3 p-3"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{source.title}</p>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[var(--text-muted)]">
            <span>{kindLabel(source.kind)}</span>
            {source.duration_sec ? <span>· {formatDuration(source.duration_sec)}</span> : null}
            {source.page_count ? <span>· {source.page_count}p</span> : null}
            <span>· {formatRelative(source.created_at)}</span>
            {source.tags.map((tag) => (
              <Badge key={tag}>{tag}</Badge>
            ))}
          </div>
          {source.status === 'failed' && source.error && (
            <p className="mt-1 line-clamp-2 text-xs text-red-500">{source.error}</p>
          )}
        </div>
        <StatusBadge status={source.status} />
      </Link>
      <button
        onClick={(event) => {
          event.preventDefault()
          setMenuOpen(!menuOpen)
        }}
        className="absolute right-2 top-1/2 hidden -translate-y-1/2 rounded p-1.5 text-[var(--text-muted)] opacity-0 transition-opacity hover:bg-[var(--bg-sunken)] group-hover:opacity-100 sm:block"
        aria-label="Actions"
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {menuOpen && (
        <>
          <button className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} aria-hidden />
          <div className="absolute right-2 top-11 z-20 w-40 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] py-1 shadow-xl">
            <button
              onClick={() => {
                setMenuOpen(false)
                onDelete()
              }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-red-500 hover:bg-[var(--bg-sunken)]"
            >
              <Trash2 className="h-3.5 w-3.5" /> Delete
            </button>
          </div>
        </>
      )}
    </Card>
  )
}

interface TreeNode {
  folder: Folder
  children: TreeNode[]
}

function buildTree(folders: Folder[]): TreeNode[] {
  const nodes = new Map<number, TreeNode>(
    folders.map((folder) => [folder.id, { folder, children: [] }]),
  )
  const roots: TreeNode[] = []
  for (const node of nodes.values()) {
    const parent = node.folder.parent_id != null ? nodes.get(node.folder.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  return roots
}

function FolderNode({
  node, depth, activeId, onSelect,
}: {
  node: TreeNode
  depth: number
  activeId: number | undefined
  onSelect: (id: number) => void
}) {
  const [open, setOpen] = useState(true)
  return (
    <div>
      <div className="flex items-center" style={{ paddingLeft: depth * 10 }}>
        {node.children.length > 0 ? (
          <button
            onClick={() => setOpen(!open)}
            className="rounded p-0.5 text-[var(--text-muted)] hover:bg-[var(--bg-sunken)]"
            aria-label="Toggle"
          >
            <ChevronRight className={cn('h-3 w-3 transition-transform', open && 'rotate-90')} />
          </button>
        ) : (
          <span className="w-4" />
        )}
        <FolderRow
          label={node.folder.name}
          icon={FolderIcon}
          count={node.folder.source_count}
          active={activeId === node.folder.id}
          onClick={() => onSelect(node.folder.id)}
          colour={node.folder.color}
        />
      </div>
      {open && node.children.map((child) => (
        <FolderNode
          key={child.folder.id}
          node={child}
          depth={depth + 1}
          activeId={activeId}
          onSelect={onSelect}
        />
      ))}
    </div>
  )
}

function FolderRow({
  label, icon: Icon, count, active, onClick, colour,
}: {
  label: string
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>
  count: number
  active: boolean
  onClick: () => void
  colour?: string | null
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] transition-colors',
        active
          ? 'bg-[var(--accent-soft)] font-medium text-[var(--accent)]'
          : 'text-[var(--text-muted)] hover:bg-[var(--bg-sunken)] hover:text-[var(--text)]',
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" style={colour ? { color: colour } : undefined} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {count > 0 && <span className="shrink-0 text-[10px] tabular-nums opacity-60">{count}</span>}
    </button>
  )
}
