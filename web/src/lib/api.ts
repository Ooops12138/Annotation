import type { LearningDocument, ReviewReport } from './document'

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()
const baseUrl = (configuredBaseUrl || 'http://127.0.0.1:8000').replace(/\/$/, '')

export interface LibraryDocumentSummary {
  document_id: string
  title: string
  book_id?: string | null
  current_version_id?: string | null
  version?: number | null
  document_status?: string | null
  review_status?: string | null
  run_id?: string | null
  created_at?: string | null
  updated_at?: string | null
  [key: string]: unknown
}

export interface LibraryBook {
  book_id: string
  title?: string | null
  original_filename?: string | null
  sha256?: string | null
  stored_path?: string | null
  page_count?: number | null
  block_count?: number | null
  created_at?: string | null
  updated_at?: string | null
  documents?: LibraryDocumentSummary[]
  [key: string]: unknown
}

export interface LibraryResponse {
  books: LibraryBook[]
  documents: LibraryDocumentSummary[]
}

export interface SourceLocation {
  source_ref: string
  page_number: number
  block_index: number
}
export type SourceLocations = Record<string, SourceLocation>

export interface WorkflowRunResult {
  source_locations?: SourceLocations
  status?: string
  run_id?: string
  book_id?: string
  document?: LearningDocument | null
  document_id?: string | null
  review_report?: ReviewReport | null
  run?: Record<string, unknown>
  progress?: {
    stage?: string
    label?: string
    completed?: number
    total?: number
    status?: string
    phase?: string
  } | null
  errors?: string[]
  warnings?: string[]
  blueprint_loop?: {
    trace_id?: string
    attempt_count?: number
    max_attempts?: number
    final_status?: string
    stop_reason?: string
    trace_path?: string | null
  } | null
  [key: string]: unknown
}

async function fetchApi(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${baseUrl}${path}`, init)
  } catch (error) {
    const detail = error instanceof Error ? error.message : '网络连接失败'
    throw new Error(`无法连接后端 ${baseUrl}（${detail}）。请确认 API 已启动。`)
  }
}

async function readJson<T>(response: Response, fallbackMessage: string): Promise<T> {
  let payload: any = null
  try {
    payload = await response.json()
  } catch {
    // Some gateway errors have no JSON body. Keep the HTTP status in the message.
  }
  if (!response.ok) {
    const detail = Array.isArray(payload?.errors)
      ? payload.errors.join('；')
      : payload?.detail || payload?.message
    throw new Error(`${detail || fallbackMessage} (${response.status})`)
  }
  return payload as T
}

function isRecord(value: unknown): value is Record<string, any> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function documentSummary(value: unknown, bookId: string): LibraryDocumentSummary {
  if (!isRecord(value) || typeof value.document_id !== 'string' || !value.document_id.trim()
    || typeof value.title !== 'string' || !value.title.trim()) {
    throw new Error('文档库中的文档记录无效')
  }
  const currentVersion = value.current_version
  if (currentVersion !== null && !isRecord(currentVersion)) throw new Error('文档版本记录无效')
  return {
    document_id: value.document_id,
    title: value.title,
    book_id: bookId,
    current_version_id: value.current_version_id,
    version: currentVersion?.version ?? null,
    document_status: currentVersion?.document_status ?? null,
    review_status: currentVersion?.review_status ?? null,
    run_id: currentVersion?.run_id ?? null,
    created_at: value.created_at,
    updated_at: value.updated_at,
  }
}

function normalizeLibraryPayload(payload: unknown): LibraryResponse {
  if (!isRecord(payload) || !Array.isArray(payload.books)) throw new Error('文档库响应格式无效')
  const books: LibraryBook[] = payload.books.map((value: unknown) => {
    if (!isRecord(value) || typeof value.book_id !== 'string' || !Array.isArray(value.documents)) {
      throw new Error('文档库中的教材记录无效')
    }
    return { ...value, book_id: value.book_id, documents: value.documents.map((document: unknown) => documentSummary(document, value.book_id)) }
  })
  return { books, documents: books.flatMap((book) => book.documents || []) }
}

export interface DocumentResponse {
  source_locations?: SourceLocations
  status: 'ok' | 'blocked'
  document: LearningDocument | null
  review_report: ReviewReport | null
}

/** Read the saved textbook/document index without starting a model run. */
export async function getLibrary(): Promise<LibraryResponse> {
  const response = await fetchApi('/api/library')
  return normalizeLibraryPayload(await readJson(response, '文档库加载失败'))
}

/** Read one already-generated Document IR. This endpoint never invokes a model. */
export async function getDocument(documentId: string): Promise<DocumentResponse> {
  const response = await fetchApi(`/api/documents/${encodeURIComponent(documentId)}`)
  const payload = await readJson<DocumentResponse>(response, '文档加载失败')
  if (!isRecord(payload) || !['ok', 'blocked'].includes(payload.status)
    || !('document' in payload) || !('review_report' in payload)
    || (payload.status === 'ok' && !isRecord(payload.document))) {
    throw new Error('文档响应格式无效')
  }
  return payload
}

/** Store a PDF in the local document library. */
export async function uploadBook(file: File | Blob, filename?: string): Promise<LibraryBook> {
  const form = new FormData()
  const browserFile = typeof File !== 'undefined' && file instanceof File ? file : null
  form.append('file', file, filename || browserFile?.name || '教材.pdf')
  const response = await fetchApi('/api/books', { method: 'POST', body: form })
  const payload = await readJson<any>(response, '教材上传失败')
  const book = payload?.book
  if (!isRecord(book) || typeof book.book_id !== 'string') throw new Error('教材上传成功，但返回的教材记录无效')
  return book as LibraryBook
}

/** Explicitly start a new generation run for a selected textbook. */
export async function getRun(runId: string): Promise<WorkflowRunResult> {
  const response = await fetchApi(`/api/runs/${encodeURIComponent(runId)}`)
  const payload = await readJson<any>(response, '运行状态读取失败')
  if (!isRecord(payload) || typeof payload.status !== 'string') throw new Error('运行响应格式无效')
  return payload as WorkflowRunResult
}

export async function getPromptProfiles(): Promise<Array<{ id: string; label: string }>> {
  const response = await fetchApi('/api/prompt-profiles')
  const payload = await readJson<any>(response, '学科选项加载失败')
  if (!Array.isArray(payload?.profiles) || !payload.profiles.every((item: any) =>
    typeof item?.id === 'string' && typeof item?.label === 'string')) throw new Error('学科选项格式无效')
  return payload.profiles
}

export async function startRun(
  bookId: string,
  onSnapshot?: (snapshot: WorkflowRunResult) => void,
  existingRunId?: string,
  promptProfile?: string,
): Promise<WorkflowRunResult> {
  const response = existingRunId ? await fetchApi(`/api/runs/${encodeURIComponent(existingRunId)}`) : await fetchApi('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ book_id: bookId, async_run: true, run_id: existingRunId, prompt_profile: promptProfile }),
  })
  let payload = await readJson<any>(response, '学习文档生成失败')
  if (!isRecord(payload) || typeof payload.status !== 'string') throw new Error('运行响应格式无效')
  let snapshot = payload as WorkflowRunResult
  onSnapshot?.(snapshot)
  const runId = typeof snapshot.run_id === 'string' ? snapshot.run_id : (snapshot.run as any)?.run_id
  while (runId && ['queued', 'running', 'stopping'].includes(snapshot.status || '')) {
    await new Promise((resolve) => window.setTimeout(resolve, 900))
    snapshot = await getRun(runId)
    onSnapshot?.(snapshot)
  }
  if (snapshot.status === 'error' || snapshot.status === 'failed' || snapshot.progress?.status === 'failed') {
    throw new Error(Array.isArray(snapshot.errors) ? snapshot.errors.join('；') : String(snapshot.error || snapshot.run?.error || '学习文档生成失败'))
  }
  return snapshot
}

export async function stopRun(runId: string): Promise<WorkflowRunResult> {
  const response = await fetchApi(`/api/runs/${encodeURIComponent(runId)}/stop`, { method: 'POST' })
  const payload = await readJson<any>(response, '停止请求失败')
  if (!isRecord(payload) || typeof payload.status !== 'string') throw new Error('停止响应格式无效')
  return payload as WorkflowRunResult
}

export async function resumeRun(runId: string): Promise<{ status: string; run_id: string }> {
  const response = await fetchApi(`/api/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST' })
  const payload = await readJson<any>(response, '续跑请求失败')
  if (!isRecord(payload) || typeof payload.run_id !== 'string') throw new Error('续跑响应格式无效')
  return payload as { status: string; run_id: string }
}
