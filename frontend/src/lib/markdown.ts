/**
 * Local patch: a small markdown parser for chat answers.
 *
 * The model answers in markdown (bold, lists, tables, `$maths$`, `[12]` citations) and the
 * chat printed it verbatim. This covers what the model actually writes rather than all of
 * CommonMark, and returns a tree instead of HTML so nothing the model writes is ever injected
 * into the page. Unclosed markers stay literal, which keeps a half-streamed answer readable.
 */

export type Inline =
  | { t: 'text'; v: string }
  | { t: 'bold'; c: Inline[] }
  | { t: 'italic'; c: Inline[] }
  | { t: 'code'; v: string }
  | { t: 'math'; v: string }
  | { t: 'cite'; id: number }
  | { t: 'br' }

export interface ListItem {
  depth: number
  c: Inline[]
}

export type Block =
  | { t: 'p'; c: Inline[] }
  | { t: 'h'; level: number; c: Inline[] }
  | { t: 'list'; ordered: boolean; start: number; items: ListItem[] }
  | { t: 'code'; lang: string; v: string }
  | { t: 'math'; v: string }
  | { t: 'table'; head: Inline[][]; rows: Inline[][][] }

// Earliest match wins, so maths and code claim their `*` and `[n]` before emphasis or
// citations can. Single-dollar maths must not start or end on a space and must not be
// followed by a digit, so "$5 and $10" stays a price.
const INLINE = new RegExp(
  [
    '`([^`\\n]+)`',
    '\\$\\$([^$]+)\\$\\$',
    '\\$([^\\s$](?:[^$\\n]*[^\\s$])?)\\$(?!\\d)',
    '\\*\\*(.+?)\\*\\*',
    '__(.+?)__',
    '\\*([^\\s*](?:[^*\\n]*[^\\s*])?)\\*',
    '\\[(\\d+)\\]',
  ].join('|'),
  'g',
)

export function parseInline(source: string): Inline[] {
  const out: Inline[] = []
  const pushText = (v: string) => {
    if (!v) return
    const prev = out[out.length - 1]
    if (prev?.t === 'text') prev.v += v
    else out.push({ t: 'text', v })
  }
  let last = 0
  for (const match of source.matchAll(INLINE)) {
    const start = match.index ?? 0
    pushText(source.slice(last, start))
    const [, code, display, math, bold, boldUnderscore, italic, cite] = match
    if (code !== undefined) out.push({ t: 'code', v: code })
    else if (display !== undefined) out.push({ t: 'math', v: display.trim() })
    else if (math !== undefined) out.push({ t: 'math', v: math })
    else if (bold !== undefined || boldUnderscore !== undefined)
      out.push({ t: 'bold', c: parseInline(bold ?? boldUnderscore) })
    else if (italic !== undefined) out.push({ t: 'italic', c: parseInline(italic) })
    else out.push({ t: 'cite', id: Number(cite) })
    last = start + match[0].length
  }
  pushText(source.slice(last))
  return out
}

/** Lines joined by hard breaks, for paragraphs and list items that wrap. */
function inlineLines(lines: string[]): Inline[] {
  return lines.flatMap((line, index) =>
    index === 0 ? parseInline(line) : [{ t: 'br' } as Inline, ...parseInline(line)],
  )
}

const FENCE = /^\s*```\s*([\w+-]*)\s*$/
const HEADING = /^\s*(#{1,6})\s+(.*?)\s*#*\s*$/
const ITEM = /^(\s*)([-*+]|(\d+)[.)])\s+(.*)$/
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/

function cells(line: string): Inline[][] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  return trimmed.split('|').map((cell) => parseInline(cell.trim()))
}

export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let paragraph: string[] = []
  let list: Extract<Block, { t: 'list' }> | null = null
  let itemLines: string[] = []

  const flushItem = () => {
    if (list && itemLines.length) list.items[list.items.length - 1].c = inlineLines(itemLines)
    itemLines = []
  }
  const flush = () => {
    if (paragraph.length) blocks.push({ t: 'p', c: inlineLines(paragraph) })
    paragraph = []
    flushItem()
    list = null
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    const fence = FENCE.exec(line)
    if (fence) {
      flush()
      const body: string[] = []
      while (++i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i])
      blocks.push({ t: 'code', lang: fence[1], v: body.join('\n') })
      continue
    }

    const trimmed = line.trim()
    if (trimmed.startsWith('$$')) {
      flush()
      const single = /^\$\$(.+)\$\$$/.exec(trimmed)
      if (single) {
        blocks.push({ t: 'math', v: single[1].trim() })
        continue
      }
      const body = [trimmed.slice(2)]
      while (++i < lines.length) {
        const end = lines[i].indexOf('$$')
        if (end >= 0) {
          body.push(lines[i].slice(0, end))
          break
        }
        body.push(lines[i])
      }
      blocks.push({ t: 'math', v: body.join('\n').trim() })
      continue
    }

    if (!trimmed) {
      // A blank line ends a paragraph; a list survives it if another item follows.
      if (paragraph.length) flush()
      else if (list && !ITEM.test(lines[i + 1] ?? '')) flush()
      continue
    }

    if (RULE.test(line)) {
      flush()
      continue
    }

    const heading = HEADING.exec(line)
    if (heading) {
      flush()
      blocks.push({ t: 'h', level: heading[1].length, c: parseInline(heading[2]) })
      continue
    }

    if (trimmed.startsWith('|') && TABLE_SEPARATOR.test(lines[i + 1] ?? '')) {
      flush()
      const head = cells(line)
      const rows: Inline[][][] = []
      i++
      while (i + 1 < lines.length && lines[i + 1].trim().startsWith('|')) rows.push(cells(lines[++i]))
      blocks.push({ t: 'table', head, rows })
      continue
    }

    const item = ITEM.exec(line)
    if (item) {
      if (paragraph.length) flush()
      flushItem()
      if (!list) {
        list = { t: 'list', ordered: item[3] !== undefined, start: Number(item[3] ?? 1), items: [] }
        blocks.push(list)
      }
      list.items.push({ depth: Math.min(Math.floor(item[1].length / 2), 3), c: [] })
      itemLines = [item[4]]
      continue
    }

    if (list && /^\s/.test(line)) {
      itemLines.push(trimmed) // an indented continuation of the current item
      continue
    }
    if (list) flush()
    paragraph.push(trimmed)
  }
  flush()
  return blocks
}
