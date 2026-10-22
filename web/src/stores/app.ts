import { defineStore } from 'pinia'
import {
  getDocument,
  getLibrary,
  startRun,
  uploadBook,
  type LibraryBook,
  type LibraryDocumentSummary,
  type WorkflowRunResult,
} from '../lib/api'

function messageFrom(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
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
    reviewReport: null as any,
    run: null as WorkflowRunResult | null,
    libraryLoading: false,
    loading: false,
    uploading: false,
    generating: false,
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
      this.error = ''
      this.selectedBookId = bookId
      try {
        const result = await startRun(bookId)
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
  },
})
