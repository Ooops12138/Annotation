import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import katex from 'katex'
import type { StateBlock, StateInline, Token } from 'markdown-it'
import 'katex/dist/katex.min.css'

function isEscaped(source: string, index: number): boolean {
  let slashCount = 0
  for (let cursor = index - 1; cursor >= 0 && source[cursor] === '\\'; cursor -= 1) {
    slashCount += 1
  }
  return slashCount % 2 === 1
}

function findClosingDelimiter(source: string, start: number, delimiter: string): number {
  for (let index = start; index < source.length; index += 1) {
    if (source.startsWith(delimiter, index) && !isEscaped(source, index)) return index
    if (source[index] === '\\') {
      index += 1
      continue
    }
  }
  return -1
}

const inlineMathRule = (state: StateInline, silent: boolean): boolean => {
  const start = state.pos
  const source = state.src
  let opener: '$' | '$$' | '\\(' | null = null

  if (source.startsWith('$$', start) && !isEscaped(source, start)) {
    opener = '$$'
  } else if (source[start] === '$' && source[start + 1] !== '$' && !isEscaped(source, start)) {
    opener = '$'
  } else if (source.startsWith('\\(', start) && !isEscaped(source, start)) {
    opener = '\\('
  }
  if (!opener) return false

  const bodyStart = start + opener.length
  if (bodyStart >= state.posMax || source[bodyStart] === '\n' || /\s/.test(source[bodyStart])) return false
  const closer = opener === '$' || opener === '$$' ? opener : '\\)'
  const close = findClosingDelimiter(source, bodyStart, closer)
  if (close < 0 || close === bodyStart || source[close - 1] === '\n') return false
  if (opener === '$' && /\s/.test(source[close - 1])) return false
  if (silent) return true

  const token = state.push(opener === '$$' ? 'math_block_inline' : 'math_inline', 'span', 0)
  token.content = source.slice(bodyStart, close)
  token.markup = opener
  state.pos = close + closer.length
  return true
}

function lineText(state: StateBlock, line: number): string {
  const start = state.bMarks[line] + state.tShift[line]
  return state.src.slice(start, state.eMarks[line])
}

const blockMathRule = (state: StateBlock, startLine: number, endLine: number, silent: boolean): boolean => {
  if (state.sCount[startLine] - state.blkIndent >= 4) return false
  const firstLine = lineText(state, startLine).trim()
  let opener: '$$' | '\\[' | null = null

  if (firstLine === '$$' || (firstLine.startsWith('$$') && firstLine.endsWith('$$') && firstLine.length > 4)) {
    opener = '$$'
  } else if (firstLine === '\\[' || (firstLine.startsWith('\\[') && firstLine.endsWith('\\]') && firstLine.length > 4)) {
    opener = '\\['
  }
  if (!opener) return false
  if (silent) return true

  const closer = opener === '$$' ? '$$' : '\\]'
  let closeLine = startLine
  let content = ''
  if (firstLine.length > opener.length && firstLine.endsWith(closer)) {
    content = firstLine.slice(opener.length, -closer.length).trim()
    closeLine = startLine + 1
  } else {
    const bodyLines: string[] = []
    closeLine = startLine + 1
    while (closeLine < endLine) {
      if (lineText(state, closeLine).trim() === closer) break
      bodyLines.push(lineText(state, closeLine))
      closeLine += 1
    }
    if (closeLine >= endLine) return false
    content = bodyLines.join('\n').trim()
    closeLine += 1
  }

  const token = state.push('math_block', 'div', 0)
  token.block = true
  token.content = content
  token.markup = opener
  token.map = [startLine, closeLine]
  state.line = closeLine
  return true
}

function renderKatex(latex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(latex, {
      displayMode,
      throwOnError: false,
      trust: false,
    })
  } catch {
    return '<span class="annotation-formula-error">公式暂时无法渲染，请回看教材来源。</span>'
  }
}

const markdown = new MarkdownIt({
  html: false,
  linkify: false,
  typographer: false,
  breaks: true,
})

markdown.block.ruler.before('fence', 'math_block', blockMathRule)
markdown.inline.ruler.before('text', 'math_inline', inlineMathRule)
markdown.renderer.rules.math_inline = (tokens: Token[], index: number) =>
  '<span class="annotation-inline-formula">' + renderKatex(tokens[index].content, false) + '</span>'
markdown.renderer.rules.math_block_inline = (tokens: Token[], index: number) =>
  '<span class="annotation-inline-formula-display">' + renderKatex(tokens[index].content, true) + '</span>'
markdown.renderer.rules.math_block = (tokens: Token[], index: number) =>
  '<div class="annotation-inline-formula-display">' + renderKatex(tokens[index].content, true) + '</div>\n'

const allowedTags = [
  'p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li',
  'a', 'h1', 'h2', 'h3', 'h4', 'hr',
  'span', 'math', 'semantics', 'mrow', 'mi', 'mn', 'mo', 'msup', 'msub', 'msubsup',
  'mfrac', 'msqrt', 'mroot', 'mtext', 'mstyle', 'mspace', 'mpadded', 'mover',
  'munder', 'munderover', 'menclose', 'annotation', 'mtable', 'mtr', 'mtd',
  'svg', 'path', 'line', 'rect', 'g',
]

const allowedAttrs = [
  'href', 'title', 'target', 'rel', 'class', 'style', 'xmlns', 'encoding', 'aria-hidden',
  'mathvariant', 'stretchy', 'symmetric', 'minsize', 'maxsize', 'accent', 'accentunder',
  'bevelled', 'columnalign', 'columnspan', 'rowalign', 'rowspan', 'scriptlevel',
  'display', 'width', 'height', 'viewBox', 'preserveAspectRatio', 'd', 'fill', 'stroke',
  'stroke-width',
]

type Sanitizer = {
  sanitize: (html: string, config: Record<string, unknown>) => string
}
type SanitizerFactory = ((window: Window) => Sanitizer) & Partial<Sanitizer>

function sanitize(html: string): string {
  const purifier = DOMPurify as unknown as SanitizerFactory
  const config = {
    ALLOWED_TAGS: allowedTags,
    ALLOWED_ATTR: allowedAttrs,
    FORBID_ATTR: ['id', 'onclick', 'onerror', 'onload', 'onmouseover'],
    ALLOW_DATA_ATTR: false,
  }
  if (typeof purifier.sanitize === 'function') return purifier.sanitize(html, config)
  if (typeof window !== 'undefined') return purifier(window).sanitize(html, config)
  return html
}

export function renderMarkdown(content: string): string {
  return sanitize(markdown.render(content || ''))
}

/**
 * Render text that is embedded in an existing inline element (for example a
 * heading or a quiz option). Using renderInline avoids introducing paragraph
 * elements while preserving the same math and sanitization rules.
 */
export function renderMarkdownInline(content: string): string {
  return sanitize(markdown.renderInline(content || ''))
}
