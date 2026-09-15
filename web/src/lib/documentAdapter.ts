import type { LearningDocument, ReviewReport, ReviewIssue } from './document'

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function string(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function issues(value: unknown): ReviewIssue[] {
  if (!Array.isArray(value)) return []
  return value.filter(isRecord).flatMap((issue) => {
    const issueId = string(issue.issue_id)
    const message = string(issue.message)
    const category = string(issue.category)
    const severity = issue.severity
    if (!issueId || !message || !category || !['info', 'warning', 'blocking'].includes(String(severity))) return []
    return [{
      issue_id: issueId,
      category,
      severity: severity as ReviewIssue['severity'],
      message,
      target_id: string(issue.target_id),
      layer: ['fact', 'stance', 'structure'].includes(String(issue.layer)) ? issue.layer as ReviewIssue['layer'] : undefined,
      source_refs: Array.isArray(issue.source_refs) ? issue.source_refs.filter((ref): ref is string => typeof ref === 'string') : [],
      suggested_action: string(issue.suggested_action),
    }]
  })
}

function sourceRefs(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined
  return value.filter((ref): ref is string => typeof ref === 'string')
}

function legacyFormulaContent(latex: string): string {
  return `$$\n${latex}\n$$`
}

function legacyExampleContent(title: string, problem: string, solution: string): string {
  return `### ${title || '例题'}\n\n**题目**\n\n${problem}\n\n**解答**\n\n${solution}`
}

function normalizeLegacyDocumentNode(value: unknown): unknown {
  if (!isRecord(value)) return value
  const type = string(value.type)
  const id = string(value.id)
  const refs = sourceRefs(value.source_refs)

  if (type === 'formula' && id) {
    const latex = string(value.latex)
    if (latex !== null) {
      return {
        type: 'markdown' as const,
        id,
        content: legacyFormulaContent(latex),
        ...(refs === undefined ? {} : { source_refs: refs }),
      }
    }
  }

  if (type === 'example' && id) {
    const title = string(value.title)
    const problem = string(value.problem)
    const solution = string(value.solution)
    if (title !== null && problem !== null && solution !== null) {
      return {
        type: 'markdown' as const,
        id,
        content: legacyExampleContent(title, problem, solution),
        ...(refs === undefined ? {} : { source_refs: refs }),
      }
    }
  }

  return value
}

export function normalizeLearningDocument(value: unknown): LearningDocument | null {
  if (!isRecord(value)) return null
  const artifactId = string(value.artifact_id)
  const documentId = string(value.document_id)
  const runId = string(value.run_id)
  const createdBy = string(value.created_by)
  const blueprintVersion = string(value.blueprint_version)
  const title = string(value.title)
  const version = number(value.version)
  if (!artifactId || !documentId || !runId || !createdBy || !blueprintVersion || !title || version === null || !Array.isArray(value.sections)) return null
  const sections = value.sections.filter(isRecord).flatMap((section) => {
    const id = string(section.id)
    const sectionTitle = string(section.title)
    if (!id || !sectionTitle || !Array.isArray(section.children)) return []
    return [{
      type: 'section' as const,
      id,
      title: sectionTitle,
      children: section.children.map(normalizeLegacyDocumentNode),
    }]
  })
  return {
    artifact_id: artifactId,
    document_id: documentId,
    run_id: runId,
    version,
    status: string(value.status) ?? 'unknown',
    source_refs: Array.isArray(value.source_refs) ? value.source_refs.filter((ref): ref is string => typeof ref === 'string') : [],
    created_by: createdBy,
    issues: issues(value.issues),
    blueprint_version: blueprintVersion,
    title,
    sections,
    review_report_id: string(value.review_report_id),
  }
}

export function normalizeReviewReport(value: unknown): ReviewReport | null {
  if (!isRecord(value)) return null
  const artifactId = string(value.artifact_id)
  const reportId = string(value.report_id)
  const runId = string(value.run_id)
  const createdBy = string(value.created_by)
  const documentId = string(value.document_id)
  const reviewedArtifactId = string(value.reviewed_artifact_id)
  const version = number(value.version)
  const status = value.status
  if (!artifactId || !reportId || !runId || !createdBy || !documentId || !reviewedArtifactId || version === null || !['passed', 'at_risk', 'blocked', 'needs_revision'].includes(String(status))) return null
  return {
    artifact_id: artifactId,
    report_id: reportId,
    run_id: runId,
    version,
    status: status as ReviewReport['status'],
    created_by: createdBy,
    document_id: documentId,
    reviewed_artifact_id: reviewedArtifactId,
    source_refs: Array.isArray(value.source_refs) ? value.source_refs.filter((ref): ref is string => typeof ref === 'string') : [],
    issues: issues(value.issues),
    issue_counts: isRecord(value.issue_counts) ? Object.fromEntries(Object.entries(value.issue_counts).filter(([, count]) => typeof count === 'number')) : {},
    checks: isRecord(value.checks) ? Object.fromEntries(Object.entries(value.checks).filter(([, check]) => typeof check === 'string')) : {},
    human_review_required: value.human_review_required === true,
    revision_of: string(value.revision_of),
    recommendations: Array.isArray(value.recommendations) ? value.recommendations.filter((item): item is string => typeof item === 'string') : [],
    artifact_path: string(value.artifact_path),
  }
}
