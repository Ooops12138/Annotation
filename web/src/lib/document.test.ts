import { describe, expect, it } from 'vitest'
import { isInteractiveComponentSpec, isDocumentNode, isSupportedDocumentNodeType, reviewStatusLabel, supportedDocumentNodeTypes } from './document'
import { collectRejectedNodes, rejectedNodeToReviewIssue } from './documentRegistry'
import { normalizeLearningDocument, normalizeReviewReport } from './documentAdapter'

describe('Document IR presentation contract', () => {
  it('keeps the renderer node allow-list explicit', () => {
    expect(supportedDocumentNodeTypes).toEqual(['markdown', 'callout', 'quiz', 'interactive_component'])
    expect(isSupportedDocumentNodeType('quiz')).toBe(true)
    expect(isSupportedDocumentNodeType('interactive_component')).toBe(true)
    expect(isSupportedDocumentNodeType('formula')).toBe(false)
    expect(isSupportedDocumentNodeType('example')).toBe(false)
    expect(isSupportedDocumentNodeType('<script>')).toBe(false)
  })

  it('accepts only complete, typed nodes at the renderer boundary', () => {
    expect(isDocumentNode({ type: 'markdown', id: 'm-1', content: '正文' })).toBe(true)
    expect(isDocumentNode({ type: 'quiz', id: 'q-1', question: '问题', options: ['A', 'B'], answer: 'A', explanation: '解释' })).toBe(true)
    expect(isDocumentNode({ type: 'quiz', id: 'q-single', question: '问题', options: ['A'], answer: 'A', explanation: '解释' })).toBe(false)
    expect(isDocumentNode({ type: 'quiz', id: 'q-duplicate', question: '问题', options: ['A', 'A'], answer: 'A', explanation: '解释' })).toBe(false)
    expect(isDocumentNode({ type: 'quiz', id: 'q-blank-option', question: '问题', options: ['A', ' '], answer: 'A', explanation: '解释' })).toBe(false)
    expect(isDocumentNode({ type: 'quiz', id: 'q-2', question: '问题', options: ['A'], answer: 'A' })).toBe(false)
    expect(isDocumentNode({ type: 'formula', id: 'f-1', latex: 'x^2' })).toBe(false)
    expect(isDocumentNode({ type: 'example', id: 'e-1', title: '例题', problem: '题目', solution: '解答' })).toBe(false)
    expect(isDocumentNode({ type: 'html', id: 'x-1', content: '<script>alert(1)</script>' })).toBe(false)
    expect(isDocumentNode({
      type: 'interactive_component', id: 'ic-1', artifact_id: 'artifact-1',
      spec: {
        component_type: 'generated_html', component_id: 'line-1', title: '区间', learning_objective: '区分上确界与最大元', source_refs: ['src-1'],
        accessibility: { aria_label: '区间数轴', description: '右端点开放', observation: '没有最大元' }, controls: [], test_actions: [], annotations: [],
        html: '<p>学习观察</p>', css: '', javascript: '',
      },
    })).toBe(true)
    expect(isDocumentNode({ type: 'interactive_component', id: 'ic-unsafe', artifact_id: 'artifact-1', spec: { component_type: 'function_graph', formula: 'import("x")' } })).toBe(false)
  })

  it('turns unknown or malformed nodes into visible review records', () => {
    const rejected = collectRejectedNodes([{ children: [
      { type: 'html', id: 'unsafe-1', content: '<script>alert(1)</script>' },
      { type: 'quiz', id: 'broken-1', question: '缺字段', options: [] },
    ] }])
    expect(rejected).toHaveLength(2)
    expect(rejected[0].reason).toBe('unsupported_node_type')
    expect(rejected[1].reason).toBe('invalid_node_shape')
    expect(rejectedNodeToReviewIssue(rejected[0]).severity).toBe('blocking')
  })

  it('preserves legacy nodes for the backend migration boundary', () => {
    const document = normalizeLearningDocument({
      artifact_id: 'doc-1', document_id: 'doc-1', run_id: 'run-1', version: 1, status: 'published', created_by: 'fixture',
      blueprint_version: 'bp-1:v1', title: '学习文档', source_refs: ['src-1'],
      sections: [{ id: 'sec-1', title: '第一节', children: [
        { type: 'formula', id: 'formula-1', latex: 'x^2', source_refs: ['src-1'] },
        { type: 'example', id: 'example-1', title: '平方', problem: '计算 $2^2$', solution: '$2^2 = 4$' },
        { type: 'html', id: 'unsafe-1' },
      ] }],
    })
    expect(document?.sections[0].children).toEqual([
      { type: 'formula', id: 'formula-1', latex: 'x^2', source_refs: ['src-1'] },
      { type: 'example', id: 'example-1', title: '平方', problem: '计算 $2^2$', solution: '$2^2 = 4$' },
      { type: 'html', id: 'unsafe-1' },
    ])
    expect(normalizeLearningDocument({ title: '缺少契约' })).toBeNull()
  })

  it('rejects malformed review reports at the API boundary', () => {
    expect(normalizeReviewReport({ report_id: 'r-1' })).toBeNull()
    expect(normalizeReviewReport({
      artifact_id: 'a-1', report_id: 'r-1', run_id: 'run-1', version: 1, status: 'at_risk', created_by: 'reviewer',
      document_id: 'd-1', reviewed_artifact_id: 'd-1', issues: [{ issue_id: 'i-1', category: 'uncertainty', severity: 'warning', message: '建议核实' }],
    })?.issues).toHaveLength(1)
  })

  it('uses neutral labels for review outcomes', () => {
    expect(reviewStatusLabel('at_risk')).toBe('带风险呈现')
    expect(reviewStatusLabel('blocked')).toBe('审核阻塞')
    expect(reviewStatusLabel('needs_revision')).toBe('需要修订')
    expect(reviewStatusLabel('fixture')).toBe('审核状态待确认')
  })
})


describe('local integrity isolation', () => {
  const root = {
    artifact_id: 'a', document_id: 'd', run_id: 'r', version: 1, status: 'published',
    created_by: 'test', blueprint_version: 'b:v1', title: 'Large textbook', issues: [],
  }
  const section = (index: number) => ({ id: `s-${index}`, title: `Chapter ${index}`, children: [{ type: 'markdown', id: `m-${index}`, content: `Readable ${index}` }] })
  const report = { artifact_id: 'review', report_id: 'review', run_id: 'r', version: 1, status: 'passed', created_by: 'reviewer', document_id: 'd', reviewed_artifact_id: 'a', issues: [] }

  it('keeps all 1000 positions when a middle chapter is malformed', () => {
    const sections: unknown[] = Array.from({ length: 1000 }, (_, i) => section(i))
    sections[500] = { id: 'broken', title: 'Damaged chapter', children: null }
    const value = { ...root, sections }
    const result = normalizeLearningDocument(value, report)!
    expect(result.sections).toHaveLength(1000)
    expect(result.sections[500].unavailable).toBe(true)
    expect(result.sections[499].children).toEqual(section(499).children)
    expect(result.sections[501].children).toEqual(section(501).children)
    expect(result.integrity_issues[0].diagnostic_path).toBe('document.sections[500]')
    expect(value.sections[500]).toEqual({ id: 'broken', title: 'Damaged chapter', children: null })
  })

  it('records every malformed review issue and scopes the known target', () => {
    const review = { ...report, issues: [{ issue_id: 'bad', target_id: 'm-0', severity: 'blocking' }, 123] }
    const result = normalizeLearningDocument({ ...root, sections: [section(0), section(1)] }, review)!
    expect(result.unavailable_targets).toEqual(['m-0'])
    expect(result.integrity_issues.map((error) => error.diagnostic_path)).toEqual(['review_report.issues[0]', 'review_report.issues[1]'])
    expect(result.sections[1].children).toEqual(section(1).children)
    expect(normalizeReviewReport(review)?.status).toBe('at_risk')
  })

  it('rejects one bad node without dropping its healthy siblings', () => {
    const children = [section(0).children[0], { type: 'script', id: 'bad' }, section(1).children[0]]
    const result = normalizeLearningDocument({ ...root, sections: [{ ...section(0), children }] }, report)!
    expect(result.sections[0].children).toEqual(children)
    expect(result.integrity_issues[0].diagnostic_path).toBe('document.sections[0].children[1]')
  })

  it('reserves total rejection for unrecognizable root structures', () => {
    expect(normalizeLearningDocument({ ...root, sections: null }, report)).toBeNull()
    expect(normalizeLearningDocument({ ...root, sections: [null] }, report)?.sections[0].unavailable).toBe(true)
  })

  it('does not trust a missing, corrupt or mismatched audit report', () => {
    for (const review of [null, { status: 'passed' }, { ...report, document_id: 'other' }]) {
      expect(normalizeLearningDocument({ ...root, sections: [section(0)] }, review)?.integrity_issues.length).toBeGreaterThan(0)
    }
  })

  it('isolates duplicate section identifiers instead of breaking navigation', () => {
    const result = normalizeLearningDocument({ ...root, sections: [section(0), section(0), section(1)] }, report)!
    expect(new Set(result.sections.map((s) => s.id)).size).toBe(3)
    expect(result.sections[0].unavailable).toBe(true)
    expect(result.sections[1].unavailable).toBe(true)
    expect(result.sections[2].unavailable).toBeUndefined()
  })

  it('shows a shared document and report issue once', () => {
    const issue = { issue_id: 'shared', category: 'uncertainty', severity: 'warning', message: 'Verify this claim' }
    const result = normalizeLearningDocument({ ...root, issues: [issue], sections: [section(0)] }, { ...report, issues: [issue] })!
    expect(result.issues).toEqual([issue])
  })
})


describe('component library boundary', () => {
  const spec = {
    component_type: 'generated_html', component_id: 'library', title: 'Observe', learning_objective: 'Compare',
    source_refs: ['src-1'], accessibility: { aria_label: 'Compare', description: 'States', observation: 'Observe' },
    controls: [], test_actions: [], annotations: [], html: '<div>State</div>', css: '', javascript: '',
  }
  it('accepts native, each local library and their combination', () => {
    for (const libraries of [[], ['jsxgraph'], ['echarts'], ['d3'], ['jsxgraph', 'echarts', 'd3']]) {
      expect(isInteractiveComponentSpec({ ...spec, libraries })).toBe(true)
    }
  })
  it('rejects unknown and repeated libraries', () => {
    for (const libraries of [['remote'], ['d3', 'd3'], ['echarts', 'https://example.com/script.js']]) {
      expect(isInteractiveComponentSpec({ ...spec, libraries })).toBe(false)
    }
  })
})
