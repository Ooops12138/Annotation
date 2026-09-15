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
  status?: string | null
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

export interface WorkflowRunResult {
  status?: string
  run_id?: string
  book_id?: string
  document?: any
  document_id?: string | null
  review_report?: any
  blueprint?: any
  run?: Record<string, unknown>
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

function documentSummary(value: unknown, bookId?: string): LibraryDocumentSummary | null {
  if (!isRecord(value) || typeof value.document_id !== 'string') return null
  const title = typeof value.title === 'string' && value.title.trim()
    ? value.title
    : '未命名学习文档'
  const currentVersion = isRecord(value.current_version) ? value.current_version : null
  return {
    ...value,
    document_id: value.document_id,
    title,
    book_id: value.book_id ?? bookId,
    version: value.version ?? currentVersion?.version ?? null,
    document_status: value.document_status ?? currentVersion?.document_status ?? null,
    review_status: value.review_status ?? currentVersion?.review_status ?? null,
    run_id: value.run_id ?? currentVersion?.run_id ?? null,
  }
}

function normalizeLibraryPayload(payload: unknown): LibraryResponse {
  // The repository returns books with nested documents. Accept the flattened
  // shape as well so the API boundary remains stable during migration.
  const source = isRecord(payload) ? payload.library ?? payload.data ?? payload : payload
  const rawBooks = Array.isArray(source) ? source : isRecord(source) && Array.isArray(source.books) ? source.books : []
  const books: LibraryBook[] = rawBooks.flatMap((value) => {
    if (!isRecord(value) || typeof value.book_id !== 'string') return []
    const nested = Array.isArray(value.documents)
      ? value.documents.flatMap((document) => documentSummary(document, value.book_id) || [])
      : []
    return [{ ...value, book_id: value.book_id, documents: nested }]
  })
  const flatDocuments = isRecord(source) && Array.isArray(source.documents)
    ? source.documents.flatMap((document) => documentSummary(document) || [])
    : []
  // Some API versions expose documents only at the top level. Attach those
  // summaries back to their textbook so the sidebar remains usable regardless
  // of whether the server returns nested or flattened records.
  const documentsByBook = new Map<string, LibraryDocumentSummary[]>()
  for (const document of flatDocuments) {
    if (!document.book_id) continue
    const current = documentsByBook.get(document.book_id) || []
    current.push(document)
    documentsByBook.set(document.book_id, current)
  }
  for (const book of books) {
    const nested = book.documents || []
    const merged = [...nested, ...(documentsByBook.get(book.book_id) || [])]
    book.documents = merged.filter((document, index, all) => all.findIndex((item) => item.document_id === document.document_id) === index)
  }
  const documents = [...books.flatMap((book) => book.documents || []), ...flatDocuments]
  const uniqueDocuments = documents.filter((document, index, all) => all.findIndex((item) => item.document_id === document.document_id) === index)
  return { books, documents: uniqueDocuments }
}

export async function getHealth(): Promise<{ status: string; service: string; version: string }> {
  const response = await fetchApi('/health')
  return readJson(response, '健康检查失败')
}

export async function getDemoDocument(): Promise<any> {
  const response = await fetchApi('/api/demo-document')
  return readJson(response, '文档加载失败')
}

/** Read the saved textbook/document index without starting a model run. */
export async function getLibrary(): Promise<LibraryResponse> {
  const response = await fetchApi('/api/library')
  return normalizeLibraryPayload(await readJson(response, '文档库加载失败'))
}

/** Read one already-generated Document IR. This endpoint never invokes a model. */
export async function getDocument(documentId: string): Promise<any> {
  const encodedId = encodeURIComponent(documentId)
  const response = await fetchApi(`/api/documents/${encodedId}`)
  const payload = await readJson<any>(response, '文档加载失败')
  const envelope = payload?.document ? payload : payload?.data?.document ? payload.data : null
  if (envelope?.document) {
    // Keep the audit report available to the store while preserving the
    // renderer's Document IR shape (unknown fields are ignored by its adapter).
    return envelope.review_report
      ? { ...envelope.document, review_report: envelope.review_report }
      : envelope.document
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
  const book = payload?.book ?? payload?.data?.book ?? payload
  if (!isRecord(book) || typeof book.book_id !== 'string') throw new Error('教材上传成功，但返回的教材记录无效')
  return book as LibraryBook
}

/** Explicitly start a new generation run for a selected textbook. */
export async function startRun(bookId: string): Promise<WorkflowRunResult> {
  const response = await fetchApi('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ book_id: bookId }),
  })
  const payload = await readJson<any>(response, '学习文档生成失败')
  const result = (payload?.run && isRecord(payload.run)) ? { ...payload.run, ...payload } : payload
  if (result?.status === 'error' || result?.status === 'failed') {
    throw new Error(Array.isArray(result.errors) ? result.errors.join('；') : result.error || '学习文档生成失败')
  }
  return result as WorkflowRunResult
}

/** Legacy compatibility endpoint; the library UI never calls this function. */
export async function runWorkflow(): Promise<WorkflowRunResult> {
  const response = await fetchApi('/api/workflow/run', { method: 'POST' })
  const payload = await readJson<WorkflowRunResult>(response, '工作流运行失败')
  if (payload.status === 'error') throw new Error(payload.errors?.join('；') ?? '工作流运行失败')
  return payload
}
