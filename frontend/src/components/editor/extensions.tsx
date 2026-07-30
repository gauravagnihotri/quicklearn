import { Node, mergeAttributes } from '@tiptap/core'
import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer } from '@tiptap/react'
import type { NodeViewProps } from '@tiptap/react'
import katex from 'katex'
import { AlertTriangle, Key, Lightbulb, ListChecks, Quote } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'

/**
 * Custom nodes beyond TipTap's defaults: block maths, Mermaid diagrams and callouts.
 *
 * Every node also carries `segmentIds`, which is what draws the source anchor in the gutter.
 * Declaring it on the schema — rather than storing it in a side table keyed by position — is
 * what makes it survive editing: ProseMirror maps the attribute along with the node when text
 * above it changes.
 */

const SEGMENT_ATTR = {
  segmentIds: {
    default: [] as number[],
    parseHTML: (element: HTMLElement) => {
      const raw = element.getAttribute('data-segment-ids')
      if (!raw) return []
      return raw.split(',').map(Number).filter((n) => !Number.isNaN(n))
    },
    renderHTML: (attributes: Record<string, unknown>) => {
      const ids = attributes.segmentIds as number[] | undefined
      if (!ids?.length) return {}
      return { 'data-segment-ids': ids.join(',') }
    },
  },
}

// ---------------------------------------------------------------- maths

function MathView({ node, updateAttributes, editor }: NodeViewProps) {
  const latex = String(node.attrs.latex ?? '')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(latex)
  const [error, setError] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => setDraft(latex), [latex])

  useEffect(() => {
    if (!containerRef.current || editing) return
    try {
      katex.render(latex || '\\;', containerRef.current, {
        displayMode: true,
        throwOnError: false,
        // Rendering an error inline is more useful than an exception: the user can see which
        // formula the model got wrong and fix it in place.
        errorColor: '#dc2626',
        strict: false,
      })
      setError(null)
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : 'Invalid LaTeX')
    }
  }, [latex, editing])

  const editable = editor.isEditable

  return (
    <NodeViewWrapper className="ql-math" data-drag-handle>
      {editing && editable ? (
        <div className="space-y-1.5">
          <textarea
            autoFocus
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => {
              updateAttributes({ latex: draft })
              setEditing(false)
            }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setDraft(latex)
                setEditing(false)
              }
              if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                updateAttributes({ latex: draft })
                setEditing(false)
              }
            }}
            rows={Math.min(8, draft.split('\n').length + 1)}
            className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-sunken)] p-2 font-mono text-xs"
          />
          <p className="text-[10px] text-[var(--text-muted)]">
            LaTeX · Esc to cancel · {navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+Enter to save
          </p>
        </div>
      ) : (
        <div
          ref={containerRef}
          onClick={() => editable && setEditing(true)}
          className={editable ? 'cursor-text' : undefined}
          title={editable ? 'Click to edit' : undefined}
        />
      )}
      {error && <p className="mt-1 text-[10px] text-red-500">{error}</p>}
    </NodeViewWrapper>
  )
}

export const MathBlock = Node.create({
  name: 'mathBlock',
  group: 'block',
  atom: true,
  draggable: true,

  addAttributes: () => ({ latex: { default: '' }, ...SEGMENT_ATTR }),

  parseHTML: () => [{ tag: 'div[data-type="math-block"]' }],
  renderHTML: ({ HTMLAttributes }) => [
    'div',
    mergeAttributes(HTMLAttributes, { 'data-type': 'math-block' }),
  ],
  addNodeView: () => ReactNodeViewRenderer(MathView),
})

// ---------------------------------------------------------------- mermaid

let mermaidPromise: Promise<typeof import('mermaid').default> | null = null

async function getMermaid(dark: boolean) {
  // Loaded on demand: mermaid is a large dependency and most notes contain no diagrams.
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then((module) => module.default)
  }
  const mermaid = await mermaidPromise
  mermaid.initialize({
    startOnLoad: false,
    theme: dark ? 'dark' : 'default',
    securityLevel: 'strict',
    fontFamily: 'ui-sans-serif, system-ui, sans-serif',
  })
  return mermaid
}

function MermaidView({ node, updateAttributes, editor }: NodeViewProps) {
  const code = String(node.attrs.code ?? '')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(code)
  const [svg, setSvg] = useState('')
  const [error, setError] = useState<string | null>(null)
  const id = useId().replace(/:/g, '_')

  useEffect(() => setDraft(code), [code])

  useEffect(() => {
    let cancelled = false
    if (!code.trim()) {
      setSvg('')
      return
    }
    const dark = document.documentElement.classList.contains('dark')
    getMermaid(dark)
      .then((mermaid) => mermaid.render(`m${id}`, code))
      .then((result) => {
        if (!cancelled) {
          setSvg(result.svg)
          setError(null)
        }
      })
      .catch((exception) => {
        if (!cancelled) {
          setSvg('')
          setError(exception instanceof Error ? exception.message : 'Could not render diagram')
        }
      })
    return () => {
      cancelled = true
    }
  }, [code, id])

  const editable = editor.isEditable

  return (
    <NodeViewWrapper className="ql-mermaid" data-drag-handle>
      {editing && editable ? (
        <div className="w-full space-y-1.5">
          <textarea
            autoFocus
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => {
              updateAttributes({ code: draft })
              setEditing(false)
            }}
            rows={Math.min(14, draft.split('\n').length + 1)}
            className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-sunken)] p-2 font-mono text-xs"
          />
          <p className="text-[10px] text-[var(--text-muted)]">Mermaid syntax · click away to save</p>
        </div>
      ) : error ? (
        <div className="w-full">
          <p className="mb-1 text-[11px] text-amber-600 dark:text-amber-400">
            This diagram could not be rendered.
          </p>
          <pre className="overflow-x-auto rounded bg-[var(--bg-sunken)] p-2 text-[11px]">{code}</pre>
        </div>
      ) : (
        <div
          onClick={() => editable && setEditing(true)}
          className={editable ? 'cursor-pointer' : undefined}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      )}
    </NodeViewWrapper>
  )
}

export const MermaidBlock = Node.create({
  name: 'mermaidBlock',
  group: 'block',
  atom: true,
  draggable: true,

  addAttributes: () => ({ code: { default: '' }, ...SEGMENT_ATTR }),

  parseHTML: () => [{ tag: 'div[data-type="mermaid-block"]' }],
  renderHTML: ({ HTMLAttributes }) => [
    'div',
    mergeAttributes(HTMLAttributes, { 'data-type': 'mermaid-block' }),
  ],
  addNodeView: () => ReactNodeViewRenderer(MermaidView),
})

// ---------------------------------------------------------------- callout

const CALLOUT_META: Record<string, { label: string; icon: typeof Key }> = {
  key: { label: 'Key point', icon: Key },
  warning: { label: 'Careful', icon: AlertTriangle },
  example: { label: 'Example', icon: Lightbulb },
  cue: { label: 'Cue', icon: ListChecks },
  summary: { label: 'Summary', icon: Quote },
}

function CalloutView({ node }: NodeViewProps) {
  const variant = String(node.attrs.variant ?? 'key')
  const meta = CALLOUT_META[variant] ?? CALLOUT_META.key
  const Icon = meta.icon
  return (
    <NodeViewWrapper className="ql-callout" data-variant={variant}>
      <span className="ql-callout-label" contentEditable={false}>
        <Icon className="mr-1 inline h-3 w-3" />
        {meta.label}
      </span>
      <NodeViewContent />
    </NodeViewWrapper>
  )
}

export const Callout = Node.create({
  name: 'callout',
  group: 'block',
  content: 'block+',
  defining: true,

  addAttributes: () => ({ variant: { default: 'key' }, ...SEGMENT_ATTR }),

  parseHTML: () => [{ tag: 'div[data-type="callout"]' }],
  renderHTML: ({ HTMLAttributes }) => [
    'div',
    mergeAttributes(HTMLAttributes, { 'data-type': 'callout' }),
    0,
  ],
  addNodeView: () => ReactNodeViewRenderer(CalloutView),
})

// ---------------------------------------------------------------- segment ids on core nodes

/**
 * Adds `segmentIds` to the built-in block nodes.
 *
 * Without this the attribute would be stripped on the first edit, silently severing every
 * citation link in the document — the exact failure the anchoring feature exists to prevent.
 */
export const SegmentAnchors = Node.create({
  name: 'segmentAnchors',

  addGlobalAttributes() {
    return [
      {
        types: [
          'paragraph', 'heading', 'bulletList', 'orderedList', 'codeBlock', 'blockquote', 'table',
        ],
        attributes: SEGMENT_ATTR,
      },
    ]
  },
})
