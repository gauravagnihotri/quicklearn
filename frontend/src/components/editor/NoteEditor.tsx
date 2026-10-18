import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight'
import Link from '@tiptap/extension-link'
import Placeholder from '@tiptap/extension-placeholder'
import Table from '@tiptap/extension-table'
import TableCell from '@tiptap/extension-table-cell'
import TableHeader from '@tiptap/extension-table-header'
import TableRow from '@tiptap/extension-table-row'
import TaskItem from '@tiptap/extension-task-item'
import TaskList from '@tiptap/extension-task-list'
import { BubbleMenu, EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { common, createLowlight } from 'lowlight'
import {
  Check, Loader2, Maximize2, Minimize2, Sparkles, Wand2, Scissors, BookOpen, Layers,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../../lib/api'
import type { TipTapDoc } from '../../lib/types'
import { cn, debounce } from '../../lib/utils'
import { Button, Spinner, Tooltip } from '../ui'
import { Callout, InlineMath, MathBlock, MermaidBlock, SegmentAnchors } from './extensions'

const lowlight = createLowlight(common)

const AI_ACTIONS = [
  { id: 'explain', label: 'Explain', icon: BookOpen },
  { id: 'expand', label: 'Expand', icon: Maximize2 },
  { id: 'simplify', label: 'Simplify', icon: Minimize2 },
  { id: 'key_points', label: 'Key points', icon: Scissors },
] as const

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

export function NoteEditor({
  noteId,
  doc,
  editable = true,
  onAnchorClick,
  onDirty,
}: {
  noteId: number
  doc: TipTapDoc
  editable?: boolean
  /** Called when a gutter anchor is clicked, with the segments backing that block. */
  onAnchorClick?: (segmentIds: number[]) => void
  onDirty?: () => void
}) {
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [aiBusy, setAiBusy] = useState<string | null>(null)
  const skipNextUpdate = useRef(true)

  const save = useRef(
    // 800ms debounce, per PRD §7.2. Long enough not to fire mid-word, short enough that a
    // browser crash loses at most a sentence.
    debounce((noteIdArg: number, json: unknown) => {
      setSaveState('saving')
      api
        .saveNote(noteIdArg, json)
        .then(() => setSaveState('saved'))
        .catch((error) => {
          setSaveState('error')
          toast.error(error instanceof ApiError ? error.message : 'Could not save your changes.')
        })
    }, 800),
  ).current

  const editor = useEditor(
    {
      editable,
      extensions: [
        StarterKit.configure({
          codeBlock: false,
          heading: { levels: [1, 2, 3, 4] },
        }),
        CodeBlockLowlight.configure({ lowlight }),
        Link.configure({ openOnClick: false, autolink: true }),
        Placeholder.configure({ placeholder: 'Start writing, or type / for commands…' }),
        Table.configure({ resizable: true }),
        TableRow,
        TableHeader,
        TableCell,
        TaskList,
        TaskItem.configure({ nested: true }),
        MathBlock,
        MermaidBlock,
        Callout,
        SegmentAnchors,
        InlineMath,
      ],
      content: doc,
      editorProps: {
        attributes: { class: 'prose-note focus:outline-none' },
      },
      onUpdate: ({ editor: instance }) => {
        // The initial content load fires onUpdate; saving then would mark a pristine note
        // dirty the moment it is opened, and dirty notes warn before regeneration.
        if (skipNextUpdate.current) {
          skipNextUpdate.current = false
          return
        }
        onDirty?.()
        save(noteId, instance.getJSON())
      },
    },
    [noteId],
  )

  // Replace content when the note changes underneath us (regeneration, version switch).
  useEffect(() => {
    if (!editor) return
    skipNextUpdate.current = true
    editor.commands.setContent(doc, false)
  }, [editor, doc])

  useEffect(() => {
    editor?.setEditable(editable)
  }, [editor, editable])

  // Flush pending edits on unmount and on tab close, so navigating away never loses a save.
  useEffect(() => {
    const flush = () => {
      if (editor && saveState === 'saving') save.flush(noteId, editor.getJSON())
    }
    window.addEventListener('beforeunload', flush)
    return () => {
      window.removeEventListener('beforeunload', flush)
      save.cancel()
    }
  }, [editor, noteId, save, saveState])

  // Gutter anchors: rendered by decorating each block with a button positioned in the margin.
  // Done with a DOM effect rather than a ProseMirror plugin because the anchors are pure
  // presentation and must not enter the document or the undo history.
  const containerRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const container = containerRef.current
    if (!container || !editor) return

    const paint = () => {
      container.querySelectorAll('.ql-anchor').forEach((node) => node.remove())
      const blocks = container.querySelectorAll<HTMLElement>('.ProseMirror > *')
      blocks.forEach((block) => {
        const raw = block.getAttribute('data-segment-ids')
        if (!raw) return
        const ids = raw.split(',').map(Number).filter((n) => !Number.isNaN(n))
        if (!ids.length) return

        block.classList.add('ql-anchored')
        const button = document.createElement('button')
        button.className = 'ql-anchor'
        button.type = 'button'
        button.title = 'Jump to the source of this passage'
        button.setAttribute('contenteditable', 'false')
        button.innerHTML =
          '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>'
        button.onclick = (event) => {
          event.preventDefault()
          event.stopPropagation()
          onAnchorClick?.(ids)
        }
        block.appendChild(button)
      })
    }

    paint()
    editor.on('update', paint)
    editor.on('selectionUpdate', paint)
    return () => {
      editor.off('update', paint)
      editor.off('selectionUpdate', paint)
    }
  }, [editor, onAnchorClick, doc])

  const runAiAction = useCallback(
    async (action: string) => {
      if (!editor) return
      const { from, to } = editor.state.selection
      const selection = editor.state.doc.textBetween(from, to, ' ')
      if (selection.trim().length < 3) {
        toast.error('Select some text first.')
        return
      }
      setAiBusy(action)
      try {
        if (action === 'make_cards') {
          const result = await api.makeCardsFromSelection(noteId, selection)
          toast.success(`Created ${result.created} flashcard${result.created === 1 ? '' : 's'}.`)
        } else {
          const context = editor.state.doc.textBetween(
            Math.max(0, from - 600),
            Math.min(editor.state.doc.content.size, to + 600),
            ' ',
          )
          const result = await api.aiAction(noteId, action, selection, context)
          if (action === 'explain') {
            // Explain adds a note rather than replacing the text being explained.
            editor.chain().focus().insertContentAt(to, `\n\n${result.result}`).run()
          } else {
            editor.chain().focus().insertContentAt({ from, to }, result.result).run()
          }
        }
      } catch (error) {
        toast.error(error instanceof ApiError ? error.message : 'That action failed.')
      } finally {
        setAiBusy(null)
      }
    },
    [editor, noteId],
  )

  if (!editor) {
    return (
      <div className="flex items-center gap-2 py-10 text-sm text-[var(--text-muted)]">
        <Spinner /> Loading editor…
      </div>
    )
  }

  return (
    <div ref={containerRef} className="relative">
      {editable && (
        <div className="sticky top-0 z-10 -mx-1 mb-2 flex items-center gap-2 bg-[var(--bg)]/85 px-1 py-1.5 backdrop-blur">
          <SaveIndicator state={saveState} />
          <div className="flex-1" />
          <span className="hidden text-[11px] text-[var(--text-muted)] sm:block">
            Select text for AI actions
          </span>
        </div>
      )}

      {editable && (
        <BubbleMenu
          editor={editor}
          tippyOptions={{ duration: 120, placement: 'top' }}
          className="flex items-center gap-0.5 rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] p-1 shadow-xl"
        >
          {AI_ACTIONS.map((action) => (
            <Tooltip key={action.id} label={action.label}>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => runAiAction(action.id)}
                disabled={aiBusy !== null}
              >
                {aiBusy === action.id ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <action.icon className="h-3.5 w-3.5" />
                )}
              </Button>
            </Tooltip>
          ))}
          <span className="mx-0.5 h-4 w-px bg-[var(--border)]" />
          <Tooltip label="Make flashcards">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => runAiAction('make_cards')}
              disabled={aiBusy !== null}
            >
              {aiBusy === 'make_cards' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Layers className="h-3.5 w-3.5" />
              )}
            </Button>
          </Tooltip>
          <span className="mx-0.5 h-4 w-px bg-[var(--border)]" />
          <Tooltip label="Bold">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => editor.chain().focus().toggleBold().run()}
              className={cn(editor.isActive('bold') && 'bg-[var(--bg-sunken)]')}
            >
              <span className="text-xs font-bold">B</span>
            </Button>
          </Tooltip>
          <Tooltip label="Italic">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => editor.chain().focus().toggleItalic().run()}
              className={cn(editor.isActive('italic') && 'bg-[var(--bg-sunken)]')}
            >
              <span className="text-xs italic">I</span>
            </Button>
          </Tooltip>
          <Tooltip label="Highlight as key point">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() =>
                editor.chain().focus().toggleWrap('callout', { variant: 'key' }).run()
              }
            >
              <Sparkles className="h-3.5 w-3.5" />
            </Button>
          </Tooltip>
        </BubbleMenu>
      )}

      <EditorContent editor={editor} className="pl-7" />

      {editable && <SlashHint editor={editor} />}
    </div>
  )
}

function SaveIndicator({ state }: { state: SaveState }) {
  if (state === 'saving') {
    return (
      <span className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
        <Loader2 className="h-3 w-3 animate-spin" /> Saving…
      </span>
    )
  }
  if (state === 'saved') {
    return (
      <span className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
        <Check className="h-3 w-3 text-emerald-500" /> Saved
      </span>
    )
  }
  if (state === 'error') {
    return <span className="text-[11px] text-red-500">Not saved — check your connection</span>
  }
  return <span className="text-[11px] text-[var(--text-muted)]">Autosaves as you type</span>
}

/** Minimal slash menu: insert the block types the AI can produce, so hand-editing has parity. */
function SlashHint({ editor }: { editor: NonNullable<ReturnType<typeof useEditor>> }) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null)

  useEffect(() => {
    const handler = () => {
      const { $from } = editor.state.selection
      const textBefore = $from.parent.textBetween(0, $from.parentOffset, undefined, '￼')
      if (textBefore.endsWith('/')) {
        const coords = editor.view.coordsAtPos($from.pos)
        const container = editor.view.dom.getBoundingClientRect()
        setPosition({ top: coords.bottom - container.top + 6, left: coords.left - container.left })
        setOpen(true)
      } else {
        setOpen(false)
      }
    }
    editor.on('selectionUpdate', handler)
    editor.on('update', handler)
    return () => {
      editor.off('selectionUpdate', handler)
      editor.off('update', handler)
    }
  }, [editor])

  if (!open || !position) return null

  const insert = (fn: () => void) => {
    // Remove the "/" that opened the menu before inserting.
    editor.chain().focus().deleteRange({ from: editor.state.selection.from - 1, to: editor.state.selection.from }).run()
    fn()
    setOpen(false)
  }

  const items = [
    { label: 'Heading', run: () => editor.chain().focus().toggleHeading({ level: 2 }).run() },
    { label: 'Bullet list', run: () => editor.chain().focus().toggleBulletList().run() },
    { label: 'Numbered list', run: () => editor.chain().focus().toggleOrderedList().run() },
    { label: 'Task list', run: () => editor.chain().focus().toggleTaskList().run() },
    { label: 'Code block', run: () => editor.chain().focus().toggleCodeBlock().run() },
    { label: 'Table', run: () => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
    { label: 'Maths', run: () => editor.chain().focus().insertContent({ type: 'mathBlock', attrs: { latex: 'e^{i\\pi} + 1 = 0' } }).run() },
    { label: 'Diagram', run: () => editor.chain().focus().insertContent({ type: 'mermaidBlock', attrs: { code: 'graph TD;\n  A[Start] --> B[End];' } }).run() },
    { label: 'Callout', run: () => editor.chain().focus().insertContent({ type: 'callout', attrs: { variant: 'key' }, content: [{ type: 'paragraph' }] }).run() },
  ]

  return (
    <div
      className="absolute z-30 w-48 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] py-1 shadow-xl"
      style={{ top: position.top, left: position.left }}
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          onMouseDown={(event) => {
            event.preventDefault()
            insert(item.run)
          }}
          className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-[var(--bg-sunken)]"
        >
          <Wand2 className="h-3 w-3 text-[var(--text-muted)]" />
          {item.label}
        </button>
      ))}
    </div>
  )
}
