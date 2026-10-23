import { defineStore } from 'pinia'
import {
  getDocument,
  getRun,
  getLibrary,
  startRun,
  uploadBook,
  type LibraryBook,
  type LibraryDocumentSummary,
  type WorkflowRunResult,
  type SourceLocations,
} from '../lib/api'

function messageFrom(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

function saveActiveRun(value: { run_id: string; book_id: string }) {
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem('annotation.activeRun', JSON.stringify(value))
  } catch { /* Generation can continue when browser storage is unavailable. */ }
}

function clearActiveRun() {
  try {
    if (typeof localStorage !== 'undefined') localStorage.removeItem('annotation.activeRun')
  } catch { /* Browser storage is optional. */ }
}

function readActiveRun(): { run_id?: string; book_id?: string } | null {
  if (typeof localStorage === 'undefined') return null
  try { return JSON.parse(localStorage.getItem('annotation.activeRun') || 'null') } catch {
    clearActiveRun()
    return null
  }
}

export const useAppStore = defineStore('app', {
  state: () => ({
    // Library data is intentionally separate from the selected Document IR:
    // loading this list must never trigger a model call.
    books: [] as LibraryBook[],
    documents: [] as LibraryDocumentSummary[],
    selectedBookId: null as string | null,
    selectedDocumentId: null as string | null,
    document: null as any,
    sourceLocations: {} as SourceLocations,
    reviewReport: null as any,
    run: null as WorkflowRunResult | null,
    libraryLoading: false,
    loading: false,
    uploading: false,
    generating: false,
    progress: null as WorkflowRunResult['progress'],
    error: '',
    libraryError: '',
  }),
  getters: {
    selectedBook(state): LibraryBook | null {
      return state.books.find((book) => book.book_id === state.selectedBookId) || null
    },
    selectedDocumentSummary(state): LibraryDocumentSummary | null {
      return state.documents.find((document) => document.document_id === state.selectedDocumentId) || null
    },
  },
  actions: {
    async loadLibrary() {
      this.libraryLoading = true
      this.libraryError = ''
      try {
        const result = await getLibrary()
        this.books = result.books
        this.documents = result.documents
        if (!this.selectedBookId && this.books.length) {
          this.selectedBookId = this.books[0].book_id
          // Selecting a textbook is local state only; the document is still
          // fetched only after the learner chooses a saved version.
          this.selectedDocumentId = null
          this.document = null
        } else if (this.selectedBookId && this.books.length && !this.books.some((book) => book.book_id === this.selectedBookId)) {
          this.selectedBookId = this.books[0].book_id
          this.selectedDocumentId = null
          this.document = null
        }
        return result
      } catch (error) {
        this.libraryError = messageFrom(error, '文档库暂时无法打开')
        throw error
      } finally {
        this.libraryLoading = false
      }
    },

    async selectBook(bookId: string) {
      this.selectedBookId = bookId
      this.selectedDocumentId = null
      this.document = null
      this.reviewReport = null
      this.error = ''
    },

    async selectDocument(documentId: string) {
      this.selectedDocumentId = documentId
      this.error = ''
      this.reviewReport = null
      this.loading = true
      try {
        const result = await getDocument(documentId)
        this.sourceLocations = result.source_locations ?? {}
        this.document = result.document
        this.selectedDocumentId = documentId
        this.selectedBookId = this.selectedDocumentSummary?.book_id || this.selectedBookId
        this.reviewReport = result.review_report
        if (result.status === 'blocked') this.error = '学习文档暂不可用，请稍后重试。'
        return result
      } catch (error) {
        this.document = null
        this.error = messageFrom(error, '学习文档暂时无法打开')
        throw error
      } finally {
        this.loading = false
      }
    },

    async addBook(file: File | Blob, filename?: string) {
      this.uploading = true
      this.error = ''
      try {
        const book = await uploadBook(file, filename)
        const existingIndex = this.books.findIndex((item) => item.book_id === book.book_id)
        if (existingIndex >= 0) this.books.splice(existingIndex, 1, { ...this.books[existingIndex], ...book })
        else this.books.unshift(book)
        this.selectedBookId = book.book_id
        this.selectedDocumentId = null
        this.document = null
        this.reviewReport = null
        // The server may create/restore associated documents during upload.
        // Refresh only after the explicit upload action, never on page mount.
        try {
          await this.loadLibrary()
        } catch {
          // Keep the uploaded book visible even if refreshing the index fails.
        }
        return book
      } catch (error) {
        this.error = messageFrom(error, '教材上传失败')
        throw error
      } finally {
        this.uploading = false
      }
    },

    async generate(this: any, bookId: string | null = this.selectedBookId) {
      if (!bookId) {
        const error = new Error('请先选择一本教材')
        this.error = error.message
        throw error
      }
      this.generating = true
      this.progress = { status: 'queued', label: '等待开始', completed: 0, total: 15 }
      this.error = ''
      this.selectedBookId = bookId
      try {
        const result = await startRun(bookId, (snapshot) => {
          this.run = snapshot
          this.progress = snapshot.progress || this.progress
          if (snapshot.run_id) saveActiveRun({ run_id: snapshot.run_id, book_id: bookId })
        })
        clearActiveRun()
        this.progress = result.progress || null
        this.sourceLocations = result.source_locations ?? {}
        this.run = result
        this.reviewReport = result.review_report ?? null
        if (result.status === 'blocked') {
          this.document = null
          this.selectedDocumentId = null
          this.error = '本次生成被阻塞，请稍后重试。'
        } else {
          if (!result.document) throw new Error('运行响应缺少学习文档')
          this.document = result.document
          this.selectedDocumentId = result.document.document_id
        }
        try {
          await this.loadLibrary()
        } catch {
          // The run result remains usable when a post-run index refresh fails.
        }
        return result
      } catch (error) {
        this.error = messageFrom(error, '学习文档生成失败')
        throw error
      } finally {
        this.generating = false
      }
    },

    async resumeRun(this: any) {
      const saved = readActiveRun()
      if (!saved?.run_id || !saved.book_id) return
      this.generating = true
      this.selectedBookId = saved.book_id
      try {
        const initial = await getRun(saved.run_id)
        if (initial.progress?.status !== 'running' && initial.status !== 'running') {
          clearActiveRun()
          this.run = initial
          this.sourceLocations = initial.source_locations ?? {}
          this.progress = initial.progress || null
          if (['ok', 'succeeded'].includes(initial.status || '') && initial.document) {
            this.document = initial.document
            this.selectedDocumentId = initial.document.document_id
            this.reviewReport = initial.review_report ?? null
          } else if (initial.status === 'blocked') {
            this.document = null
            this.error = '本次生成被阻塞，请稍后重试。'
          } else if (initial.status === 'failed') {
            this.error = String((initial.run as any)?.error || '学习文档生成失败')
          }
          await this.loadLibrary().catch(() => undefined)
          return
        }
        this.progress = initial.progress || null
        const result = await startRun(saved.book_id, (snapshot) => {
          this.run = snapshot
          this.progress = snapshot.progress || this.progress
        }, saved.run_id)
        clearActiveRun()
        this.progress = result.progress || null
        this.run = result
        this.sourceLocations = result.source_locations ?? {}
        if (['ok', 'succeeded'].includes(result.status || '') && result.document) {
          this.document = result.document
          this.selectedDocumentId = result.document.document_id
          this.reviewReport = result.review_report ?? null
        } else if (result.status === 'blocked') {
          this.document = null
          this.error = '本次生成被阻塞，请稍后重试。'
        }
        await this.loadLibrary().catch(() => undefined)
      } catch (error) {
        this.error = messageFrom(error, '运行状态恢复失败')
      } finally {
        this.generating = false
      }
    },
  },
})
