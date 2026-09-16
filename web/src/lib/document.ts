export const supportedDocumentNodeTypes = ['markdown', 'callout', 'quiz', 'interactive_component'] as const

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

export interface ComponentAccessibility {
  aria_label: string
  description: string
  observation: string
}

export interface ToggleControlSpec {
  kind: 'toggle'
  control_id: string
  label: string
  default_value: boolean
}

export interface RangeControlSpec {
  kind: 'range'
  control_id: string
  label: string
  minimum: number
  maximum: number
  step: number
  default_value: number
}

export type ComponentControlSpec = ToggleControlSpec | RangeControlSpec

export interface ComponentTestAction {
  action: 'toggle' | 'set_range'
  control_id: string
  value: boolean | number
  expected_text: string
}

export interface ComponentAnnotation {
  annotation_id: string
  x: number
  y: number
  text: string
}

export interface BaseInteractiveComponentSpec {
  component_id: string
  title: string
  learning_objective: string
  source_refs: string[]
  accessibility: ComponentAccessibility
  controls: ComponentControlSpec[]
  test_actions: ComponentTestAction[]
  annotations: ComponentAnnotation[]
}

export interface IntervalLineSpec extends BaseInteractiveComponentSpec {
  component_type: 'interval_line'
  interval_start: number
  interval_end: number
  left_endpoint: 'open' | 'closed'
  right_endpoint: 'open' | 'closed'
  supremum: number
  maximum: number | null
}

export interface ComplexPlanePoint {
  point_id: string
  real: number
  imaginary: number
  label: string
}

export interface ComplexPlaneSpec extends BaseInteractiveComponentSpec {
  component_type: 'complex_plane'
  points: ComplexPlanePoint[]
}

export interface FunctionDomainSpec {
  start: number
  end: number
  start_endpoint: 'open' | 'closed'
  end_endpoint: 'open' | 'closed'
}

export interface FunctionGraphSpec extends BaseInteractiveComponentSpec {
  component_type: 'function_graph'
  formula: string
  domain: FunctionDomainSpec
  sample_points: number[]
  excluded_points: number[]
  sample_count: number
}

export type InteractiveComponentSpec = IntervalLineSpec | ComplexPlaneSpec | FunctionGraphSpec

export interface InteractiveComponentNode {
  type: 'interactive_component'
  id: string
  artifact_id: string
  spec: InteractiveComponentSpec
  source_refs?: string[]
}

export type DocumentNode = MarkdownNode | CalloutNode | QuizNode | InteractiveComponentNode

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
    case 'interactive_component': return typeof node.artifact_id === 'string' && isInteractiveComponentSpec(node.spec)
  }
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isCalloutTone(value: unknown): value is CalloutNode['tone'] {
  return value === 'info' || value === 'warning' || value === 'success'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isString(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim())
}

function isAccessibility(value: unknown): value is ComponentAccessibility {
  return isRecord(value) && isString(value.aria_label) && isString(value.description) && isString(value.observation)
}

function isControl(value: unknown): value is ComponentControlSpec {
  if (!isRecord(value) || !isString(value.control_id) || !isString(value.label)) return false
  if (value.kind === 'toggle') return typeof value.default_value === 'boolean'
  return value.kind === 'range'
    && isFiniteNumber(value.minimum)
    && isFiniteNumber(value.maximum)
    && isFiniteNumber(value.step)
    && isFiniteNumber(value.default_value)
    && value.minimum < value.maximum
    && value.step > 0
    && value.default_value >= value.minimum
    && value.default_value <= value.maximum
}

function isTestAction(value: unknown, controls: Set<string>): value is ComponentTestAction {
  if (!isRecord(value) || !isString(value.control_id) || !isString(value.expected_text) || !controls.has(value.control_id)) return false
  return (value.action === 'toggle' && typeof value.value === 'boolean')
    || (value.action === 'set_range' && isFiniteNumber(value.value))
}

function isBaseComponentSpec(value: unknown): value is Record<string, unknown> & BaseInteractiveComponentSpec {
  if (!isRecord(value) || !isString(value.component_id) || !isString(value.title) || !isString(value.learning_objective)) return false
  if (!isStringArray(value.source_refs) || value.source_refs.length === 0 || !isAccessibility(value.accessibility)) return false
  if (!Array.isArray(value.controls) || !value.controls.every(isControl)) return false
  const controlIds = value.controls.map((control) => (control as ComponentControlSpec).control_id)
  if (new Set(controlIds).size !== controlIds.length) return false
  if (!Array.isArray(value.test_actions) || !value.test_actions.every((action) => isTestAction(action, new Set(controlIds)))) return false
  return Array.isArray(value.annotations) && value.annotations.every((annotation) => (
    isRecord(annotation)
    && isString(annotation.annotation_id)
    && isFiniteNumber(annotation.x)
    && isFiniteNumber(annotation.y)
    && isString(annotation.text)
  ))
}

export function isInteractiveComponentSpec(value: unknown): value is InteractiveComponentSpec {
  if (!isBaseComponentSpec(value)) return false
  if (value.component_type === 'interval_line') {
    const maximum = value.maximum
    return isFiniteNumber(value.interval_start)
      && isFiniteNumber(value.interval_end)
      && value.interval_start < value.interval_end
      && (value.left_endpoint === 'open' || value.left_endpoint === 'closed')
      && (value.right_endpoint === 'open' || value.right_endpoint === 'closed')
      && isFiniteNumber(value.supremum)
      && value.supremum === value.interval_end
      && (maximum === null || isFiniteNumber(maximum))
      && (value.right_endpoint === 'closed' ? maximum === value.interval_end : maximum === null)
  }
  if (value.component_type === 'complex_plane') {
    return Array.isArray(value.points) && value.points.length > 0 && value.points.every((point) => (
      isRecord(point) && isString(point.point_id) && isFiniteNumber(point.real) && isFiniteNumber(point.imaginary) && isString(point.label)
    ))
  }
  if (value.component_type === 'function_graph') {
    const domain = value.domain
    return isString(value.formula)
      && isRecord(domain)
      && isFiniteNumber(domain.start)
      && isFiniteNumber(domain.end)
      && domain.start < domain.end
      && (domain.start_endpoint === 'open' || domain.start_endpoint === 'closed')
      && (domain.end_endpoint === 'open' || domain.end_endpoint === 'closed')
      && Array.isArray(value.sample_points)
      && value.sample_points.length >= 2
      && value.sample_points.every(isFiniteNumber)
      && Array.isArray(value.excluded_points)
      && value.excluded_points.every(isFiniteNumber)
      && isFiniteNumber(value.sample_count)
      && Number.isInteger(value.sample_count)
      && value.sample_count >= 20
      && value.sample_count <= 400
  }
  return false
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
