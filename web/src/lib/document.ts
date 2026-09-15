export const supportedDocumentNodeTypes = ['markdown', 'callout', 'quiz'] as const

export type SupportedDocumentNodeType = (typeof supportedDocumentNodeTypes)[number]

export type ReviewSeverity = 'info' | 'warning' | 'blocking'
export type ReviewLayer = 'fact' | 'stance' | 'structure'

export interface ReviewIssue {
  issue_id: string
  category: string
  severity: ReviewSeverity
  message: string
  target_id?: string | null
  layer?: ReviewLayer
  source_refs?: string[]
  suggested_action?: string | null
}

export interface MarkdownNode {
  type: 'markdown'
  id: string
  content: string
  source_refs?: string[]
}

export interface CalloutNode {
  type: 'callout'
  id: string
  tone: 'info' | 'warning' | 'success'
  title: string
  content: string
  source_refs?: string[]
}

export interface QuizNode {
  type: 'quiz'
  id: string
  question: string
  options: string[]
  answer: string
  explanation: string
  source_refs?: string[]
}

export type DocumentNode = MarkdownNode | CalloutNode | QuizNode

export interface DocumentSection {
  type?: 'section'
  id: string
  title: string
  children: unknown[]
}

export interface LearningDocument {
  artifact_id: string
  document_id: string
  run_id: string
  version: number
  status: string
  source_refs?: string[]
  created_by: string
  issues?: ReviewIssue[]
  blueprint_version: string
  title: string
  sections: DocumentSection[]
  review_report_id?: string | null
}

export interface ReviewReport {
  artifact_id: string
  report_id: string
  run_id: string
  version: number
  status: 'passed' | 'at_risk' | 'blocked' | 'needs_revision'
  created_by: string
  document_id: string
  reviewed_artifact_id: string
  source_refs?: string[]
  issues: ReviewIssue[]
  issue_counts?: Record<string, number>
  checks?: Record<string, string>
  human_review_required?: boolean
  revision_of?: string | null
  recommendations?: string[]
  artifact_path?: string | null
}

export function isSupportedDocumentNodeType(value: unknown): value is SupportedDocumentNodeType {
  return typeof value === 'string' && (supportedDocumentNodeTypes as readonly string[]).includes(value)
}

export function isDocumentNode(value: unknown): value is DocumentNode {
  if (!value || typeof value !== 'object') return false
  const node = value as Record<string, unknown>
  if (typeof node.id !== 'string' || !isSupportedDocumentNodeType(node.type)) return false
  const optionalRefs = node.source_refs === undefined || isStringArray(node.source_refs)
  if (!optionalRefs) return false
  switch (node.type) {
    case 'markdown': return typeof node.content === 'string'
    case 'callout': return isCalloutTone(node.tone) && typeof node.title === 'string' && typeof node.content === 'string'
    case 'quiz': {
      if (typeof node.question !== 'string' || !node.question.trim()) return false
      if (!isStringArray(node.options) || node.options.length < 2) return false
      if (node.options.some((option) => !option.trim())) return false
      if (new Set(node.options).size !== node.options.length) return false
      if (typeof node.answer !== 'string' || node.options.filter((option) => option === node.answer).length !== 1) return false
      return typeof node.explanation === 'string' && Boolean(node.explanation.trim())
    }
  }
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isCalloutTone(value: unknown): value is CalloutNode['tone'] {
  return value === 'info' || value === 'warning' || value === 'success'
}

export function resolveDocumentNodeType(value: unknown): SupportedDocumentNodeType | null {
  if (!isDocumentNode(value)) return null
  return value.type
}

export function reviewStatusLabel(status: string | undefined): string {
  if (status === 'blocked') return '审核阻塞'
  if (status === 'at_risk') return '带风险呈现'
  if (status === 'needs_revision') return '需要修订'
  if (status === 'passed' || status === 'accepted' || status === 'published') return '审核通过'
  if (status === 'checking') return '审核中'
  return '审核状态待确认'
}
