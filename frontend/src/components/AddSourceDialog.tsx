import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { FileUp, Link2, Type, UploadCloud } from 'lucide-react'
import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import { cn, formatBytes } from '../lib/utils'
import {
  Button, Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger, Field, Input,
  Progress, Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Tabs, TabsContent,
  TabsList, TabsTrigger, Textarea,
} from './ui'

/** Upload, URL and paste — the three ways in that aren't recording. */
export function AddSourceDialog({
  trigger,
  defaultFolderId,
}: {
  trigger: React.ReactNode
  defaultFolderId?: number | null
}) {
  const [open, setOpen] = useState(false)
  const [folderId, setFolderId] = useState<string>(defaultFolderId ? String(defaultFolderId) : 'none')
  const [progress, setProgress] = useState<number | null>(null)
  const [dragging, setDragging] = useState(false)
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)

  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: folders } = useQuery({ queryKey: ['folders'], queryFn: () => api.listFolders() })

  const resolvedFolder = folderId === 'none' ? null : Number(folderId)

  const done = (sourceId: number) => {
    queryClient.invalidateQueries()
    setOpen(false)
    setProgress(null)
    setUrl('')
    setText('')
    setTitle('')
    toast.success('Added — processing has started.')
    navigate({ to: '/library/$sourceId', params: { sourceId: String(sourceId) } })
  }

  const fail = (error: unknown) => {
    setProgress(null)
    toast.error(error instanceof ApiError ? error.message : 'Something went wrong.')
  }

  const uploadFile = async (file: File) => {
    setProgress(0)
    try {
      const result = await api.upload(file, { folderId: resolvedFolder, onProgress: setProgress })
      done(result.source_id)
    } catch (error) {
      fail(error)
    }
  }

  const addUrl = useMutation({
    mutationFn: () => api.addUrl(url.trim(), resolvedFolder),
    onSuccess: (result) => done(result.source_id),
    onError: fail,
  })

  const addText = useMutation({
    mutationFn: () => api.addText(title.trim() || 'Pasted text', text, resolvedFolder),
    onSuccess: (result) => done(result.source_id),
    onError: fail,
  })

  return (
    <Dialog open={open} onOpenChange={(next) => !progress && setOpen(next)}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogTitle>Add a source</DialogTitle>
        <DialogDescription>
          Audio, video, PDF, Word, slides, images, a YouTube or article link, or plain text.
        </DialogDescription>

        <Tabs defaultValue="file">
          <TabsList className="mb-4 w-full">
            <TabsTrigger value="file" className="flex-1 justify-center">
              <FileUp className="h-3.5 w-3.5" /> File
            </TabsTrigger>
            <TabsTrigger value="url" className="flex-1 justify-center">
              <Link2 className="h-3.5 w-3.5" /> Link
            </TabsTrigger>
            <TabsTrigger value="text" className="flex-1 justify-center">
              <Type className="h-3.5 w-3.5" /> Text
            </TabsTrigger>
          </TabsList>

          <TabsContent value="file" className="space-y-3">
            <div
              onDragOver={(event) => {
                event.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault()
                setDragging(false)
                const file = event.dataTransfer.files[0]
                if (file) void uploadFile(file)
              }}
              onClick={() => fileInput.current?.click()}
              className={cn(
                'flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors',
                dragging
                  ? 'border-[var(--accent)] bg-[var(--accent-soft)]'
                  : 'border-[var(--border)] hover:border-[var(--accent)]/50 hover:bg-[var(--bg-sunken)]',
              )}
            >
              <UploadCloud className="h-7 w-7 text-[var(--text-muted)]" />
              <p className="text-sm font-medium">Drop a file, or click to choose</p>
              <p className="text-xs text-[var(--text-muted)]">
                mp3, wav, m4a, mp4, mov, pdf, docx, pptx, png, jpg, txt — up to 2 GB
              </p>
              <input
                ref={fileInput}
                type="file"
                className="hidden"
                accept=".mp3,.wav,.m4a,.aac,.ogg,.opus,.flac,.mp4,.mov,.mkv,.avi,.webm,.m4v,.pdf,.docx,.doc,.pptx,.ppt,.png,.jpg,.jpeg,.gif,.webp,.heic,.txt,.md,.csv"
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (file) void uploadFile(file)
                  event.target.value = ''
                }}
              />
            </div>

            {progress !== null && (
              <div className="space-y-1.5">
                <Progress value={progress} />
                <p className="text-xs text-[var(--text-muted)]">
                  {progress >= 100 ? 'Finishing upload…' : `Uploading — ${Math.round(progress)}%`}
                </p>
              </div>
            )}
          </TabsContent>

          <TabsContent value="url" className="space-y-3">
            <Field
              label="URL"
              hint="YouTube videos use existing captions when available, which is free and more accurate than transcribing."
            >
              <Input
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://youtube.com/watch?v=… or any article"
                onKeyDown={(event) => event.key === 'Enter' && url.trim() && addUrl.mutate()}
              />
            </Field>
            <Button
              variant="primary"
              className="w-full justify-center"
              disabled={!url.trim()}
              loading={addUrl.isPending}
              onClick={() => addUrl.mutate()}
            >
              Fetch and process
            </Button>
          </TabsContent>

          <TabsContent value="text" className="space-y-3">
            <Field label="Title">
              <Input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Lecture 4 — Eigenvalues"
              />
            </Field>
            <Field label="Content" hint={text ? formatBytes(new Blob([text]).size) : undefined}>
              <Textarea
                rows={8}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="Paste notes, an article, a transcript…"
              />
            </Field>
            <Button
              variant="primary"
              className="w-full justify-center"
              disabled={!text.trim()}
              loading={addText.isPending}
              onClick={() => addText.mutate()}
            >
              Process text
            </Button>
          </TabsContent>
        </Tabs>

        {(folders?.folders.length ?? 0) > 0 && (
          <div className="mt-4 border-t border-[var(--border)] pt-4">
            <Field label="Folder">
              <Select value={folderId} onValueChange={setFolderId}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">No folder</SelectItem>
                  {folders?.folders.map((folder) => (
                    <SelectItem key={folder.id} value={String(folder.id)}>
                      {folder.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
