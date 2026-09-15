import { describe, expect, it } from 'vitest'
import { renderMarkdown, renderMarkdownInline } from './markdown'

describe('learner Markdown math rendering', () => {
  it('renders explicitly delimited inline and display formulas with KaTeX', () => {
    const html = renderMarkdown(`复数可写成 $z=x+iy$，其中 $i^2=-1$。

$$|z|=\\sqrt{x^2+y^2}$$`)

    expect((html.match(/class="katex"/g) || []).length).toBeGreaterThanOrEqual(3)
    expect(html).toContain('annotation-inline-formula-display')
  })

  it('does not guess bare notation or reinterpret code spans as math', () => {
    const html = renderMarkdown('裸文本 i²=-1，以及 \`$x+1$\` 代码。')

    expect(html).not.toContain('class="katex"')
    expect(html).toContain('i²=-1')
    expect(html).toContain('$x+1$')
  })

  it('renders a display formula that follows a prose label', () => {
    const html = renderMarkdown('公式：$$x^2+y^2$$')

    expect(html).toContain('annotation-inline-formula-display')
    expect(html).toContain('class="katex-display"')
  })

  it('renders a multiline standalone formula block', () => {
    const html = renderMarkdown('$$\nx^2+y^2\n$$')

    expect(html).toContain('annotation-inline-formula-display')
    expect(html).toContain('class="katex-display"')
  })

  it('accepts the equivalent bracketed inline delimiter', () => {
    const html = renderMarkdown(String.raw`行内公式：\(x+1\)。`)

    expect(html).toContain('class="katex"')
  })

  it('renders formulas in inline node fields without introducing block paragraphs', () => {
    const html = renderMarkdownInline('选项 $x^2+y^2$')

    expect(html).toContain('class="katex"')
    expect(html).not.toContain('<p>')
  })

  it('keeps inline node fields sanitized while retaining KaTeX output', () => {
    const html = renderMarkdownInline(String.raw`$x+1$ <script>alert(1)</script>`)

    expect(html).toContain('class="katex"')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;')
  })
})
