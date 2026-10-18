import katex from 'katex'
import { Fragment } from 'react'

// Anki cloze syntax: {{c1::answer}} or {{c1::answer::hint}}.
const CLOZE = /\{\{c\d+::(.*?)(?:::(.*?))?\}\}/g
// Inline maths ($...$) or inline code (`...`). Code is matched first so a formula quoted in
// backticks still renders as maths rather than as literal dollar signs.
const INLINE = /`([^`]+)`|\$([^$]+)\$/g

/** The hidden answers of a cloze card, in order. */
export function clozeAnswers(text: string): string[] {
  return Array.from(text.matchAll(CLOZE), (match) => match[1])
}

function renderMath(latex: string, key: number) {
  const html = katex.renderToString(latex, {
    throwOnError: false,
    errorColor: '#dc2626',
    strict: false,
  })
  return <span key={key} dangerouslySetInnerHTML={{ __html: html }} />
}

/** Plain text with inline `$maths$` rendered by KaTeX and `code` in a monospace span. */
function InlineText({ text }: { text: string }) {
  const parts: React.ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(INLINE)) {
    const start = match.index ?? 0
    parts.push(text.slice(last, start))
    const [, code, latex] = match
    if (latex !== undefined) {
      parts.push(renderMath(latex, start))
    } else if (/^\$[^$]+\$$/.test(code)) {
      parts.push(renderMath(code.slice(1, -1), start))
    } else {
      parts.push(
        <code key={start} className="rounded bg-[var(--bg-sunken)] px-1 font-mono text-[0.9em]">
          {code}
        </code>,
      )
    }
    last = start + match[0].length
  }
  parts.push(text.slice(last))
  return <Fragment>{parts}</Fragment>
}

/**
 * Card text with cloze deletions blanked until `revealed`, then highlighted, and inline maths
 * and code rendered. Text without markup renders unchanged, so it is safe for every card type.
 */
export function CardText({ text, revealed }: { text: string; revealed: boolean }) {
  const parts: React.ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(CLOZE)) {
    const start = match.index ?? 0
    parts.push(<InlineText key={`t${start}`} text={text.slice(last, start)} />)
    parts.push(
      revealed ? (
        <span key={start} className="font-semibold text-[var(--accent)]">
          <InlineText text={match[1]} />
        </span>
      ) : (
        <span
          key={start}
          className="rounded bg-[var(--accent-soft)] px-1.5 font-semibold text-[var(--accent)]"
        >
          [{match[2] || '…'}]
        </span>
      ),
    )
    last = start + match[0].length
  }
  parts.push(<InlineText key="tail" text={text.slice(last)} />)
  return <Fragment>{parts}</Fragment>
}
