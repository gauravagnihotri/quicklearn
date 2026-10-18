import { Fragment } from 'react'

// Anki cloze syntax: {{c1::answer}} or {{c1::answer::hint}}.
const CLOZE = /\{\{c\d+::(.*?)(?:::(.*?))?\}\}/g

/** The hidden answers of a cloze card, in order. */
export function clozeAnswers(text: string): string[] {
  return Array.from(text.matchAll(CLOZE), (match) => match[1])
}

/**
 * Card text with cloze deletions blanked until `revealed`, then highlighted. Text without
 * cloze markup renders unchanged, so it is safe for every card type.
 */
export function ClozeText({ text, revealed }: { text: string; revealed: boolean }) {
  const parts: React.ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(CLOZE)) {
    const start = match.index ?? 0
    parts.push(text.slice(last, start))
    parts.push(
      revealed ? (
        <span key={start} className="font-semibold text-[var(--accent)]">
          {match[1]}
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
  parts.push(text.slice(last))
  return <Fragment>{parts}</Fragment>
}
