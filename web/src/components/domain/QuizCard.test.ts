import { describe, expect, it } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import QuizCard from './QuizCard.vue'

describe('quiz block Markdown', () => {
  it('renders display math, lists and fenced code in question and options', async () => {
    const content = '说明\n\n$$\nx^2+y^2\n$$\n\n- 条件一\n- 条件二\n\n```python\nx = 2\n```'
    const html = await renderToString(createSSRApp({
      render: () => h(QuizCard, { question: content, options: [content, '另一选项'], answer: content, explanation: content }),
    }))
    expect((html.match(/class="katex-display"/g) || []).length).toBe(2)
    expect((html.match(/<ul>/g) || []).length).toBe(2)
    expect((html.match(/<pre><code class="language-python">/g) || []).length).toBe(2)
    expect(html).not.toContain('<h4>')
    expect(html).toContain('aria-pressed="false"')
  })
})
