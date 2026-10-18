import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { MessageSquare, Plus, Send, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import type { ChatMessage, Excerpt, Segment } from '../lib/types'
import { openChatSocket } from '../lib/ws'
import { cn, formatRelative } from '../lib/utils'
import {
  Badge, Button, Dialog, DialogContent, DialogTitle, EmptyState, Field, Select,
  SelectContent, SelectItem, SelectTrigger, SelectValue, Spinner, Textarea,
} from '../components/ui'

export function ChatPage() {
  const search = useSearch({ from: '/chat' })
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [newOpen, setNewOpen] = useState(false)

  const { data } = useQuery({ queryKey: ['chats'], queryFn: () => api.listChats() })
  const chats = data?.chats ?? []
  const activeId = search.id ?? chats[0]?.id

  // Deep-linked from a source page: open (or create) a conversation scoped to it.
  const scopedCreate = useMutation({
    mutationFn: (sourceId: number) => api.createChat('source', sourceId),
    onSuccess: (chat) => {
      queryClient.invalidateQueries({ queryKey: ['chats'] })
      navigate({ to: '/chat', search: { id: chat.id } })
    },
  })

  const remove = useMutation({
    mutationFn: (chatId: number) => api.deleteChat(chatId),
    onSuccess: (_, chatId) => {
      queryClient.removeQueries({ queryKey: ['chat', chatId] })
      queryClient.invalidateQueries({ queryKey: ['chats'] })
      if (chatId === activeId) navigate({ to: '/chat', search: {} })
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed.'),
  })

  useEffect(() => {
    if (search.source && !search.id) {
      const existing = chats.find(
        (chat) => chat.scope_type === 'source' && chat.scope_id === search.source,
      )
      if (existing) navigate({ to: '/chat', search: { id: existing.id } })
      else if (!scopedCreate.isPending) scopedCreate.mutate(search.source)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search.source, search.id, chats.length])

  return (
    <div className="mx-auto flex h-full max-w-6xl">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-[var(--border)] md:flex">
        <div className="flex items-center justify-between p-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            Conversations
          </span>
          <Button variant="ghost" size="icon-sm" onClick={() => setNewOpen(true)}>
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
          {chats.map((chat) => (
            <div
              key={chat.id}
              className={cn(
                'group flex items-center rounded-lg transition-colors',
                chat.id === activeId
                  ? 'bg-[var(--accent-soft)]'
                  : 'hover:bg-[var(--bg-sunken)]',
              )}
            >
              <button
                onClick={() => navigate({ to: '/chat', search: { id: chat.id } })}
                className="min-w-0 flex-1 px-2.5 py-2 text-left"
              >
                <p className="truncate text-[13px] font-medium">{chat.title}</p>
                <p className="text-[10px] text-[var(--text-muted)]">
                  {chat.scope_type === 'global' ? 'Whole library' : chat.scope_type} ·{' '}
                  {formatRelative(chat.created_at)}
                </p>
              </button>
              <Button
                variant="ghost"
                size="icon-sm"
                title="Delete conversation"
                className="mr-1 shrink-0 opacity-0 focus-visible:opacity-100 group-hover:opacity-100"
                onClick={() => {
                  if (confirm(`Delete “${chat.title}”?`)) remove.mutate(chat.id)
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
          {chats.length === 0 && (
            <p className="px-2.5 py-3 text-xs text-[var(--text-muted)]">No conversations yet.</p>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {activeId ? (
          <Conversation chatId={activeId} />
        ) : (
          <div className="grid flex-1 place-items-center p-6">
            <EmptyState
              icon={MessageSquare}
              title="Chat with your notes"
              description="Ask questions across one source, a folder, or your whole library. Every answer cites the passage it came from — and if your notes don't cover it, it says so."
              action={
                <Button variant="primary" onClick={() => setNewOpen(true)}>
                  <Plus className="h-4 w-4" /> New conversation
                </Button>
              }
            />
          </div>
        )}
      </div>

      <NewChatDialog open={newOpen} onOpenChange={setNewOpen} />
    </div>
  )
}

function NewChatDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [scope, setScope] = useState<'global' | 'source' | 'folder'>('global')
  const [scopeId, setScopeId] = useState('')
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: sources } = useQuery({
    queryKey: ['sources'],
    queryFn: () => api.listSources({ status: 'ready' }),
  })
  const { data: folders } = useQuery({ queryKey: ['folders'], queryFn: () => api.listFolders() })

  const create = useMutation({
    mutationFn: () => api.createChat(scope, scope === 'global' ? null : Number(scopeId)),
    onSuccess: (chat) => {
      queryClient.invalidateQueries({ queryKey: ['chats'] })
      onOpenChange(false)
      navigate({ to: '/chat', search: { id: chat.id } })
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed.'),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogTitle>New conversation</DialogTitle>
        <div className="space-y-4">
          <Field label="Scope" hint="Narrower scopes give more focused retrieval.">
            <Select value={scope} onValueChange={(value) => setScope(value as typeof scope)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="global">Entire library</SelectItem>
                <SelectItem value="source">One source</SelectItem>
                <SelectItem value="folder">One folder</SelectItem>
              </SelectContent>
            </Select>
          </Field>

          {scope === 'source' && (
            <Field label="Source">
              <Select value={scopeId} onValueChange={setScopeId}>
                <SelectTrigger>
                  <SelectValue placeholder="Choose…" />
                </SelectTrigger>
                <SelectContent>
                  {(sources?.sources ?? []).map((source) => (
                    <SelectItem key={source.id} value={String(source.id)}>
                      {source.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}

          {scope === 'folder' && (
            <Field label="Folder">
              <Select value={scopeId} onValueChange={setScopeId}>
                <SelectTrigger>
                  <SelectValue placeholder="Choose…" />
                </SelectTrigger>
                <SelectContent>
                  {(folders?.folders ?? []).map((folder) => (
                    <SelectItem key={folder.id} value={String(folder.id)}>
                      {folder.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}

          <Button
            variant="primary"
            className="w-full justify-center"
            disabled={scope !== 'global' && !scopeId}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            Start
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function Conversation({ chatId }: { chatId: number }) {
  const queryClient = useQueryClient()
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState('')
  const [busy, setBusy] = useState(false)
  const [excerpts, setExcerpts] = useState<Excerpt[]>([])
  const [citation, setCitation] = useState<Segment | null>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const { data } = useQuery({
    queryKey: ['chat', chatId],
    queryFn: () => api.chatMessages(chatId),
  })
  const messages = data?.messages ?? []

  useEffect(() => {
    const socket = openChatSocket(chatId, {
      onExcerpts: (items) => setExcerpts(items as Excerpt[]),
      onToken: (text) => setStreaming((current) => current + text),
      onDone: () => {
        setStreaming('')
        setBusy(false)
        void queryClient.invalidateQueries({ queryKey: ['chat', chatId] })
        void queryClient.invalidateQueries({ queryKey: ['chats'] })
      },
      onError: (message) => {
        setStreaming('')
        setBusy(false)
        toast.error(message)
      },
    })
    socketRef.current = socket
    return () => {
      socket.close()
      socketRef.current = null
    }
  }, [chatId, queryClient])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, streaming])

  const send = (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || busy) return
    const socket = socketRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      toast.error('Not connected. Give it a moment and try again.')
      return
    }
    setBusy(true)
    setStreaming('')
    setExcerpts([])
    setInput('')
    // Optimistically show the question so the input clearing feels responsive.
    queryClient.setQueryData(['chat', chatId], (old: typeof data) =>
      old
        ? {
            ...old,
            messages: [
              ...old.messages,
              {
                id: -Date.now(),
                chat_id: chatId,
                role: 'user',
                content: trimmed,
                citations: [],
                followups: [],
                created_at: new Date().toISOString(),
              } as ChatMessage,
            ],
          }
        : old,
    )
    socket.send(JSON.stringify({ content: trimmed }))
  }

  const lastAssistant = [...messages].reverse().find((message) => message.role === 'assistant')

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
        <div className="mx-auto max-w-2xl space-y-5">
          {messages.length === 0 && !streaming && (
            <div className="py-8 text-center">
              <Sparkles className="mx-auto mb-2 h-6 w-6 text-[var(--text-muted)]" />
              <p className="text-sm text-[var(--text-muted)]">
                Ask a question about your material. Answers cite the passages they came from.
              </p>
            </div>
          )}

          {messages.map((message) => (
            <Bubble key={message.id} message={message} onCitation={setCitation} />
          ))}

          {streaming && (
            <div className="space-y-2">
              <MessageBody content={streaming} onCitation={setCitation} />
              <span className="inline-block h-3.5 w-1.5 animate-pulse bg-[var(--accent)]" />
            </div>
          )}

          {busy && !streaming && (
            <div className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
              <Spinner className="h-3.5 w-3.5" />
              {excerpts.length > 0
                ? `Reading ${excerpts.length} passage${excerpts.length === 1 ? '' : 's'}…`
                : 'Searching your notes…'}
            </div>
          )}

          {!busy && lastAssistant?.followups && lastAssistant.followups.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {lastAssistant.followups.map((question) => (
                <button
                  key={question}
                  onClick={() => send(question)}
                  className="rounded-full border border-[var(--border)] px-3 py-1 text-xs text-[var(--text-muted)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text)]"
                >
                  {question}
                </button>
              ))}
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      <div className="border-t border-[var(--border)] p-3 sm:p-4">
        <div className="mx-auto flex max-w-2xl items-end gap-2">
          <Textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                send(input)
              }
            }}
            rows={1}
            placeholder="Ask about your notes…"
            className="max-h-40 min-h-[2.5rem] resize-none py-2"
          />
          <Button
            variant="primary"
            size="icon"
            disabled={!input.trim() || busy}
            onClick={() => send(input)}
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <Dialog open={!!citation} onOpenChange={(open) => !open && setCitation(null)}>
        <DialogContent>
          <DialogTitle>Source passage</DialogTitle>
          {citation && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
                {citation.timestamp && <Badge tone="accent">{citation.timestamp}</Badge>}
                {citation.page_no && <Badge>page {citation.page_no}</Badge>}
                {citation.speaker && <span>{citation.speaker}</span>}
              </div>
              <p className="rounded-lg bg-[var(--bg-sunken)] p-3 text-sm leading-relaxed">
                {citation.text}
              </p>
              <Button
                variant="primary"
                className="w-full justify-center"
                onClick={() => {
                  window.location.href = `/library/${citation.source_id}?tab=transcript&seg=${citation.id}`
                }}
              >
                Open in source
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

function Bubble({
  message, onCitation,
}: {
  message: ChatMessage
  onCitation: (segment: Segment) => void
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-[var(--accent)] px-3.5 py-2 text-sm text-white">
          {message.content}
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <MessageBody content={message.content} onCitation={onCitation} />
      {message.citations.length > 0 && (
        <p className="text-[11px] text-[var(--text-muted)]">
          {message.citations.length} source
          {message.citations.length === 1 ? '' : 's'} cited — click a number to read it
        </p>
      )}
    </div>
  )
}

/**
 * Renders an answer, turning `[12]` markers into clickable citations.
 *
 * Split on the marker pattern rather than injecting HTML: the content is model output, and
 * `dangerouslySetInnerHTML` on it would be an injection vector into the user's own app.
 */
function MessageBody({
  content, onCitation,
}: {
  content: string
  onCitation: (segment: Segment) => void
}) {
  const parts = useMemo(() => content.split(/(\[\d+\])/g), [content])

  const open = async (segmentId: number) => {
    try {
      const result = await api.resolveCitations([segmentId])
      const segment = result.segments[0]
      if (segment) onCitation(segment)
      else toast.error('That passage no longer exists.')
    } catch {
      toast.error('Could not load that passage.')
    }
  }

  return (
    <div className="text-[15px] leading-relaxed">
      {parts.map((part, index) => {
        const match = /^\[(\d+)\]$/.exec(part)
        if (match) {
          const id = Number(match[1])
          return (
            <button
              key={index}
              onClick={() => open(id)}
              className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-[var(--accent-soft)] px-1 align-super text-[10px] font-medium text-[var(--accent)] transition-colors hover:bg-[var(--accent)] hover:text-white"
              title="Show the source passage"
            >
              {id}
            </button>
          )
        }
        return (
          <span key={index} className="whitespace-pre-wrap">
            {part}
          </span>
        )
      })}
    </div>
  )
}
