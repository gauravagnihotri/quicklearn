import katex from 'katex'
import { Fragment, useMemo } from 'react'
import { parseBlocks, parseInline, type Block, type Inline } from '../lib/markdown'
import { cn } from '../lib/utils'

// Local patch: renders chat answers. Only KaTeX's own output is set as HTML; everything the
// model writes goes through React as text.

function MathView({ latex, display = false }: { latex: string; display?: boolean }) {
  const html = useMemo(
    () => katex.renderToString(latex, { throwOnError: false, errorColor: '#dc2626', strict: false, displayMode: display }),
    [latex, display],
  )
  return display ? (
    <div className="overflow-x-auto py-1" dangerouslySetInnerHTML={{ __html: html }} />
  ) : (
    <span dangerouslySetInnerHTML={{ __html: html }} />
  )
}

type OnCite = (id: number) => void

function Inlines({ nodes, onCite }: { nodes: Inline[]; onCite?: OnCite }) {
  return (
    <>
      {nodes.map((node, index) => {
        switch (node.t) {
          case 'text':
            return <Fragment key={index}>{node.v}</Fragment>
          case 'br':
            return <br key={index} />
          case 'bold':
            return (
              <strong key={index} className="font-semibold">
                <Inlines nodes={node.c} onCite={onCite} />
              </strong>
            )
          case 'italic':
            return (
              <em key={index}>
                <Inlines nodes={node.c} onCite={onCite} />
              </em>
            )
          case 'code':
            return (
              <code key={index} className="rounded bg-[var(--bg-sunken)] px-1 font-mono text-[0.88em]">
                {node.v}
              </code>
            )
          case 'math':
            return <MathView key={index} latex={node.v} />
          case 'cite':
            return onCite ? (
              <button
                key={index}
                onClick={() => onCite(node.id)}
                className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-[var(--accent-soft)] px-1 align-super text-[10px] font-medium text-[var(--accent)] transition-colors hover:bg-[var(--accent)] hover:text-white"
                title="Show the source passage"
              >
                {node.id}
              </button>
            ) : (
              <Fragment key={index}>[{node.id}]</Fragment>
            )
        }
      })}
    </>
  )
}

const HEADING_SIZE = ['text-lg', 'text-base', 'text-[15px]', 'text-[15px]', 'text-sm', 'text-sm']

function BlockView({ block, onCite }: { block: Block; onCite?: OnCite }) {
  switch (block.t) {
    case 'p':
      return (
        <p>
          <Inlines nodes={block.c} onCite={onCite} />
        </p>
      )
    case 'h':
      return (
        <p className={cn('pt-1 font-semibold', HEADING_SIZE[block.level - 1])}>
          <Inlines nodes={block.c} onCite={onCite} />
        </p>
      )
    case 'list': {
      const List = block.ordered ? 'ol' : 'ul'
      return (
        <List
          start={block.ordered ? block.start : undefined}
          className={cn('space-y-1 pl-5', block.ordered ? 'list-decimal' : 'list-disc')}
        >
          {block.items.map((item, index) => (
            <li
              key={index}
              style={item.depth ? { marginLeft: `${item.depth * 1.25}rem` } : undefined}
              className={cn('pl-0.5', item.depth > 0 && 'list-[circle]')}
            >
              <Inlines nodes={item.c} onCite={onCite} />
            </li>
          ))}
        </List>
      )
    }
    case 'code':
      return (
        <pre className="overflow-x-auto rounded-lg bg-[var(--bg-sunken)] p-3 font-mono text-[13px] leading-snug">
          <code>{block.v}</code>
        </pre>
      )
    case 'math':
      return <MathView latex={block.v} display />
    case 'table':
      return (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {block.head.map((cell, index) => (
                  <th key={index} className="border-b border-[var(--border)] px-2 py-1.5 text-left font-semibold">
                    <Inlines nodes={cell} onCite={onCite} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, index) => (
                    <td key={index} className="border-b border-[var(--border)] px-2 py-1.5 align-top">
                      <Inlines nodes={cell} onCite={onCite} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
  }
}

/** A markdown answer; `[12]` markers become buttons when `onCite` is given. */
export function Markdown({ content, onCite, className }: { content: string; onCite?: OnCite; className?: string }) {
  const blocks = useMemo(() => parseBlocks(content), [content])
  return (
    <div className={cn('space-y-2.5', className)}>
      {blocks.map((block, index) => (
        <BlockView key={index} block={block} onCite={onCite} />
      ))}
    </div>
  )
}

/** One line of markdown without blocks, e.g. a suggested follow-up question. */
export function InlineMarkdown({ content }: { content: string }) {
  const nodes = useMemo(() => parseInline(content), [content])
  return <Inlines nodes={nodes} />
}
