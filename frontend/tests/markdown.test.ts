// Local patch: chat answers are markdown. Run with `npm test` (Node's own test runner).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseBlocks, parseInline } from '../src/lib/markdown.ts'

const text = (v: string) => ({ t: 'text', v })

test('bold, italic, code, maths and citations inline', () => {
  assert.deepEqual(parseInline('**Static** is *fixed*, `arr[0]` is $O(1)$ [16][25].'), [
    { t: 'bold', c: [text('Static')] },
    text(' is '),
    { t: 'italic', c: [text('fixed')] },
    text(', '),
    { t: 'code', v: 'arr[0]' },
    text(' is '),
    { t: 'math', v: 'O(1)' },
    text(' '),
    { t: 'cite', id: 16 },
    { t: 'cite', id: 25 },
    text('.'),
  ])
})

test('maths inside bold, and stars inside maths are not emphasis', () => {
  assert.deepEqual(parseInline('**cost $O(n)$** and $a*b*c$'), [
    { t: 'bold', c: [text('cost '), { t: 'math', v: 'O(n)' }] },
    text(' and '),
    { t: 'math', v: 'a*b*c' },
  ])
})

test('prices are not maths, and unclosed markers stay literal while streaming', () => {
  assert.deepEqual(parseInline('costs $5 and $10'), [text('costs $5 and $10')])
  assert.deepEqual(parseInline('**Static Arr'), [text('**Static Arr')])
})

test('a bullet star is not italic', () => {
  assert.deepEqual(parseBlocks('*   They are fixed size [16].'), [
    { t: 'list', ordered: false, start: 1, items: [{ depth: 0, c: [text('They are fixed size '), { t: 'cite', id: 16 }, text('.')] }] },
  ])
})

test('headings, paragraphs with line breaks, numbered and nested lists', () => {
  const blocks = parseBlocks('## Arrays\nline one\nline two\n\n1. first\n2. second\n   - detail\n\nafter')
  assert.deepEqual(blocks, [
    { t: 'h', level: 2, c: [text('Arrays')] },
    { t: 'p', c: [text('line one'), { t: 'br' }, text('line two')] },
    {
      t: 'list', ordered: true, start: 1,
      items: [{ depth: 0, c: [text('first')] }, { depth: 0, c: [text('second')] }, { depth: 1, c: [text('detail')] }],
    },
    { t: 'p', c: [text('after')] },
  ])
})

test('fenced code and display maths are kept verbatim, even unclosed', () => {
  assert.deepEqual(parseBlocks('```python\na = **b**\n```\n$$\nT(n) = 2T(n/2)\n$$\n```js\nlet x'), [
    { t: 'code', lang: 'python', v: 'a = **b**' },
    { t: 'math', v: 'T(n) = 2T(n/2)' },
    { t: 'code', lang: 'js', v: 'let x' },
  ])
  assert.deepEqual(parseBlocks('$$x^2$$'), [{ t: 'math', v: 'x^2' }])
})

test('tables', () => {
  assert.deepEqual(parseBlocks('| | Static | Dynamic |\n|---|---|---|\n| Size | **fixed** | grows [25] |'), [
    {
      t: 'table',
      head: [[], [text('Static')], [text('Dynamic')]],
      rows: [[[text('Size')], [{ t: 'bold', c: [text('fixed')] }], [text('grows '), { t: 'cite', id: 25 }]]],
    },
  ])
})
