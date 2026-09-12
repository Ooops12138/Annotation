import type { DocumentNode, ReviewIssue } from './document'
import { isDocumentNode, isSupportedDocumentNodeType } from './document'

export type DocumentNodeRendererKind = 'markdown' | 'formula' | 'example' | 'callout' | 'quiz'

export interface RejectedNodeRecord {
  node_id: string
  node_type: string
  reason: 'unsupported_node_type' | 'invalid_node_shape'
  message: string
}

export function resolveNodeRenderer(node: unknown): DocumentNodeRendererKind | null {
  if (!isDocumentNode(node)) return null
  return node.type
}

export function rejectedNodeRecord(node: unknown, index = 0): RejectedNodeRecord {
  const candidate = node && typeof node === 'object' ? node as Record<string, unknown> : {}
  const nodeType = typeof candidate.type === 'string' ? candidate.type : 'unknown'
  const nodeId = typeof candidate.id === 'string' ? candidate.id : `unknown-${index + 1}`
  const reason = isSupportedDocumentNodeType(nodeType) ? 'invalid_node_shape' : 'unsupported_node_type'
  return {
    node_id: nodeId,
    node_type: nodeType,
    reason,
    message: reason === 'unsupported_node_type'
      ? `节点类型“${nodeType}”不在受控白名单中，已拒绝渲染。`
      : `节点“${nodeId}”的字段不符合受控 IR 契约，已拒绝渲染。`,
  }
}

export function collectRejectedNodes(sections: Array<{ children?: unknown[] }> = []): RejectedNodeRecord[] {
  const rejected: RejectedNodeRecord[] = []
  sections.forEach((section) => {
    section.children?.forEach((node, index) => {
      if (!resolveNodeRenderer(node)) rejected.push(rejectedNodeRecord(node, index))
    })
  })
  return rejected
}

export function rejectedNodeToReviewIssue(record: RejectedNodeRecord): ReviewIssue {
  return {
    issue_id: `renderer-${record.node_id}`,
    category: 'coverage',
    severity: 'blocking',
    layer: 'structure',
    message: record.message,
    target_id: record.node_id,
    suggested_action: '修订 Document IR 后重新生成该页面。',
  }
}

export function asDocumentNode(node: unknown): DocumentNode | null {
  return isDocumentNode(node) ? node : null
}
