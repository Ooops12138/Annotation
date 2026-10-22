import { isDocumentNode } from './document'
import type { DocumentSection, LearningDocument, ReviewReport, ReviewIssue } from './document'

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}
function string(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim())
}
function refs(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(string)
}

export interface IntegrityIssue extends ReviewIssue { diagnostic_path: string }
export interface PresentedSection extends DocumentSection { unavailable?: boolean }
export interface PresentedDocument extends LearningDocument {
  sections: PresentedSection[]
  integrity_issues: IntegrityIssue[]
  unavailable_targets: string[]
}

function integrity(path: string, target?: string): IntegrityIssue {
  return {
    issue_id: `integrity-${path}`, category: 'integrity', severity: 'warning', layer: 'structure',
    message: target ? '此部分的内容或审核信息异常，暂不可用。' : '部分审核信息异常，请结合教材核查；其余章节仍可阅读。',
    target_id: target, diagnostic_path: path,
  }
}

function issues(value: unknown, path: string): { valid: ReviewIssue[]; errors: IntegrityIssue[] } {
  if (!Array.isArray(value)) return { valid: [], errors: [integrity(path)] }
  const valid: ReviewIssue[] = [], errors: IntegrityIssue[] = []
  value.forEach((issue, index) => {
    const location = `${path}[${index}]`
    if (!isRecord(issue) || !string(issue.issue_id) || !string(issue.message) || !['fact', 'logic', 'formula', 'coverage', 'source', 'transition', 'conflict', 'uncertainty', 'stance'].includes(String(issue.category))
      || !['info', 'warning', 'blocking'].includes(String(issue.severity))
      || (issue.layer !== undefined && !['fact', 'stance', 'structure'].includes(String(issue.layer)))
      || (issue.source_refs !== undefined && !refs(issue.source_refs))
      || (issue.target_id != null && !string(issue.target_id))
      || (issue.suggested_action != null && !string(issue.suggested_action))) {
      errors.push(integrity(location, isRecord(issue) && string(issue.target_id) ? issue.target_id : undefined))
    } else valid.push(issue as unknown as ReviewIssue)
  })
  return { valid, errors }
}

export function normalizeLearningDocument(value: unknown, reviewValue?: unknown): PresentedDocument | null {
  if (!isRecord(value) || !string(value.artifact_id) || !string(value.document_id) || !string(value.run_id)
    || !string(value.created_by) || !string(value.blueprint_version) || !string(value.title)
    || !Number.isInteger(value.version) || (value.version as number) < 1 || !Array.isArray(value.sections)) return null
  const parsed = issues(value.issues === undefined ? [] : value.issues, 'document.issues')
  const parsedReport = reviewValue === undefined ? null : normalizeReviewReport(reviewValue)
  const report = parsedReport?.document_id === value.document_id && parsedReport.reviewed_artifact_id === value.artifact_id ? parsedReport : null
  const errors = [...parsed.errors]
  if (reviewValue !== undefined) {
    if (!report) errors.push(integrity('review_report'))
    else errors.push(...issues(isRecord(reviewValue) ? reviewValue.issues : null, 'review_report.issues').errors)
  }
  const ids = value.sections.map((section) => isRecord(section) ? section.id : undefined)
  const occupied = new Set(ids.filter(string))
  const idCounts = new Map<string, number>()
  ids.filter(string).forEach((id) => idCounts.set(id, (idCounts.get(id) ?? 0) + 1))
  const sections: PresentedSection[] = value.sections.map((section, index) => {
    const path = `document.sections[${index}]`
    if (isRecord(section) && string(section.id) && string(section.title) && Array.isArray(section.children)
      && idCounts.get(section.id) === 1) {
      section.children.forEach((node, nodeIndex) => {
        if (!isDocumentNode(node)) errors.push(integrity(`${path}.children[${nodeIndex}]`))
      })
      return { type: 'section', id: section.id, title: section.title, children: section.children }
    }
    let id = `unavailable-section-${index + 1}`
    while (occupied.has(id)) id = `_${id}`
    occupied.add(id)
    errors.push(integrity(path, id))
    return { type: 'section', id, title: isRecord(section) && string(section.title) ? section.title : `第 ${index + 1} 节`, children: [], unavailable: true }
  })
  return {
    ...(value as unknown as LearningDocument), sections,
    issues: [...new Map([...parsed.valid, ...(report?.issues ?? [])].map((issue) => [issue.issue_id, issue])).values()],
    integrity_issues: errors,
    unavailable_targets: errors.flatMap((error) => error.target_id ? [error.target_id] : []),
  }
}

export function normalizeReviewReport(value: unknown): ReviewReport | null {
  if (!isRecord(value) || !string(value.artifact_id) || !string(value.report_id) || !string(value.run_id)
    || !string(value.created_by) || !string(value.document_id) || !string(value.reviewed_artifact_id)
    || !Number.isInteger(value.version) || (value.version as number) < 1
    || !['passed', 'at_risk', 'blocked', 'needs_revision'].includes(String(value.status))) return null
  const parsed = issues(value.issues, 'review_report.issues')
  return {
    ...(value as unknown as ReviewReport),
    status: parsed.errors.length && value.status !== 'blocked' ? 'at_risk' : value.status as ReviewReport['status'],
    issues: parsed.valid,
  }
}
