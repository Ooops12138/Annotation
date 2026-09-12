import { describe, expect, it } from 'vitest'
import { isDocumentNode, isSupportedDocumentNodeType, reviewStatusLabel, supportedDocumentNodeTypes } from './document'
import { collectRejectedNodes, rejectedNodeToReviewIssue } from './documentRegistry'
import { normalizeLearningDocument, normalizeReviewReport } from './documentAdapter'

describe('Document IR presentation contract', () => {
  it('keeps the renderer node allow-list explicit', () => {
    expect(supportedDocumentNodeTypes).toEqual(['markdown', 'formula', 'example', 'callout', 'quiz'])
    expect(isSupportedDocumentNodeType('quiz')).toBe(true)
    expect(isSupportedDocumentNodeType('<script>')).toBe(false)
  })

  it('accepts only complete, typed nodes at the renderer boundary', () => {
    expect(isDocumentNode({ type: 'markdown', id: 'm-1', content: '正文' })).toBe(true)
    expect(isDocumentNode({ type: 'quiz', id: 'q-1', question: '问题', options: ['A'], answer: 'A', explanation: '解释' })).toBe(true)
    expect(isDocumentNode({ type: 'quiz', id: 'q-2', question: '问题', options: ['A'], answer: 'A' })).toBe(false)
    expect(isDocumentNode({ type: 'formula', id: 'f-1', latex: '<script>alert(1)</script>' })).toBe(true)
    expect(isDocumentNode({ type: 'html', id: 'x-1', content: '<script>alert(1)</script>' })).toBe(false)
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

  it('adapts API payloads without dropping unknown nodes', () => {
    const document = normalizeLearningDocument({
      artifact_id: 'doc-1', document_id: 'doc-1', run_id: 'run-1', version: 1, status: 'published', created_by: 'fixture',
      blueprint_version: 'bp-1:v1', title: '学习文档', source_refs: ['src-1'],
      sections: [{ id: 'sec-1', title: '第一节', children: [{ type: 'html', id: 'unsafe-1' }] }],
    })
    expect(document?.sections[0].children).toHaveLength(1)
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
