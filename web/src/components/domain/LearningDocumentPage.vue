<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import {
  BookOpen,
  CircleAlert,
  FileText,
  LoaderCircle,
  Sparkles,
  Upload,
} from 'lucide-vue-next'
import LearningDocumentRenderer from './LearningDocumentRenderer.vue'
import { useAppStore } from '../../stores/app'
import { normalizeLearningDocument } from '../../lib/documentAdapter'
import { reviewStatusLabel, type ReviewReport } from '../../lib/document'
import type { LibraryBook, LibraryDocumentSummary } from '../../lib/api'

const app = useAppStore()
const document = computed(() => normalizeLearningDocument(app.document))
const selectedBook = computed(() => app.selectedBook)
const selectedSummary = computed(() => app.selectedDocumentSummary)

function summaryReviewStatus(summary: LibraryDocumentSummary | null): string | null {
  if (!summary) return null
  const currentVersion = summary.current_version
  if (currentVersion && typeof currentVersion === 'object') {
    const status = (currentVersion as Record<string, unknown>).review_status
    if (typeof status === 'string' && status) return status
  }
  return summary.review_status || summary.status || summary.document_status || null
}

const reviewStatus = computed(() => {
  const reportStatus = app.reviewReport && typeof app.reviewReport === 'object'
    ? (app.reviewReport as ReviewReport).status
    : null
  return reportStatus || summaryReviewStatus(selectedSummary.value)
})
const blocked = computed(() => {
  const rawStatus = app.document && typeof app.document === 'object'
    ? (app.document as Record<string, unknown>).status
    : null
  return document.value?.status === 'blocked' || reviewStatus.value === 'blocked' || rawStatus === 'blocked'
})
const atRisk = computed(() => reviewStatus.value === 'at_risk')
const hasBooks = computed(() => app.books.length > 0)
const selectedBookDocuments = computed(() => selectedBook.value?.documents || [])

function bookTitle(book: LibraryBook): string {
  return book.title || book.original_filename || book.book_id
}

function bookMeta(book: LibraryBook): string {
  const parts: string[] = []
  if (typeof book.page_count === 'number' && book.page_count > 0) parts.push(`${book.page_count} 页`)
  if (typeof book.block_count === 'number' && book.block_count > 0) parts.push(`${book.block_count} 段教材文本`)
  return parts.join(' · ') || book.original_filename || 'PDF 教材'
}

function documentTitle(summary: LibraryDocumentSummary): string {
  return summary.title || '未命名学习文档'
}

function documentStatus(summary: LibraryDocumentSummary): string {
  return summaryReviewStatus(summary) || 'pending'
}

function documentStatusLabel(summary: LibraryDocumentSummary): string {
  return reviewStatusLabel(documentStatus(summary))
}

function formatDate(value: unknown): string {
  if (typeof value !== 'string' || !value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(date)
}

async function selectDocument(documentId: string) {
  try {
    await app.selectDocument(documentId)
  } catch {
    // The store keeps the actionable error for the page alert.
  }
}

function selectBook(bookId: string) {
  void app.selectBook(bookId)
}

async function generateSelected() {
  try {
    await app.generate()
  } catch {
    // The store keeps the actionable error for the page alert.
  }
}

function isPdf(file: File): boolean {
  return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
}

async function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!isPdf(file)) {
    app.error = '请选择 PDF 格式的教材。'
    return
  }
  try {
    await app.addBook(file)
  } catch {
    // The store keeps the actionable error for the page alert.
  }
}

function handleUploadKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  const input = (event.currentTarget as HTMLElement).querySelector('input')
  input?.click()
}

onMounted(() => {
  // Library loading is read-only. No model-triggering request occurs here.
  app.load().catch(() => undefined)
})

watch(() => app.error, (error) => {
  if (error) console.error('[Annotation] 文档库操作失败:', error)
}, { immediate: true })
watch(blocked, (isBlocked) => {
  if (isBlocked) console.warn('[Annotation] 学习文档处于阻塞状态，已停止学习者呈现。')
}, { immediate: true })
</script>

<template>
  <div class="annotation-page">
    <header class="annotation-masthead">
      <div class="annotation-title-block">
        <p class="annotation-kicker">ANNOTATION / 教材档案</p>
        <div class="annotation-title-row">
          <BookOpen :size="20" stroke-width="1.5" aria-hidden="true" />
          <h1>从教材到可学习页面</h1>
        </div>
        <p class="annotation-dek">从已保存的学习文档继续阅读，或为一本新教材生成学习页面。</p>
      </div>
    </header>

    <div class="annotation-rule" aria-hidden="true"></div>

    <section class="library-workspace" aria-label="教材文档库">
      <aside class="library-sidebar">
        <div class="library-sidebar-heading">
          <div>
            <p class="library-label">DOCUMENT LIBRARY</p>
            <h2>我的教材</h2>
          </div>
          <label class="upload-control" role="button" tabindex="0" :aria-disabled="app.uploading" :class="{ 'is-disabled': app.uploading }" @keydown="handleUploadKeydown">
            <Upload :size="15" stroke-width="1.7" aria-hidden="true" />
            <span>{{ app.uploading ? '上传中…' : '上传 PDF' }}</span>
            <input type="file" accept="application/pdf,.pdf" :disabled="app.uploading" @change="handleUpload">
          </label>
        </div>

        <div v-if="app.libraryLoading" class="library-status" role="status" aria-live="polite">
          <LoaderCircle class="spin" :size="15" stroke-width="1.7" aria-hidden="true" />
          <span>正在读取文档库…</span>
        </div>
        <div v-else-if="app.libraryError" class="library-status library-status-error" role="alert">
          <CircleAlert :size="15" stroke-width="1.7" aria-hidden="true" />
          <span>{{ app.libraryError }}</span>
        </div>
        <div v-else-if="!hasBooks" class="library-empty" role="status">
          <BookOpen :size="18" stroke-width="1.5" aria-hidden="true" />
          <p>文档库还是空的。</p>
          <span>上传教材后，生成的学习文档会保存在这里。</span>
        </div>

        <ul v-else class="book-list">
          <li v-for="book in app.books" :key="book.book_id" class="book-list-item">
            <button
              type="button"
              class="book-item"
              :class="{ 'is-selected': app.selectedBookId === book.book_id }"
              :aria-pressed="app.selectedBookId === book.book_id"
              @click="selectBook(book.book_id)"
            >
              <span class="book-item-icon"><BookOpen :size="17" stroke-width="1.5" aria-hidden="true" /></span>
              <span class="book-item-copy">
                <strong>{{ bookTitle(book) }}</strong>
                <small>{{ bookMeta(book) }}</small>
              </span>
              <time v-if="formatDate(book.updated_at || book.created_at)" :datetime="String(book.updated_at || book.created_at)">{{ formatDate(book.updated_at || book.created_at) }}</time>
            </button>

            <ul v-if="app.selectedBookId === book.book_id && (book.documents || []).length" class="document-list" aria-label="已生成文档">
              <li v-for="summary in book.documents" :key="summary.document_id">
                <button
                  type="button"
                  class="document-item"
                  :class="{ 'is-selected': app.selectedDocumentId === summary.document_id }"
                  :aria-current="app.selectedDocumentId === summary.document_id ? 'page' : undefined"
                  @click="selectDocument(summary.document_id)"
                >
                  <FileText :size="14" stroke-width="1.6" aria-hidden="true" />
                  <span>{{ documentTitle(summary) }}</span>
                  <small :class="`status-${documentStatus(summary)}`">{{ documentStatusLabel(summary) }}</small>
                </button>
              </li>
            </ul>
          </li>
        </ul>
      </aside>

      <section class="library-content" aria-live="polite">
        <header v-if="selectedBook" class="library-content-heading">
          <div>
            <p class="library-label">当前教材</p>
            <h2>{{ bookTitle(selectedBook) }}</h2>
            <p class="library-content-meta">{{ bookMeta(selectedBook) }}</p>
          </div>
          <button type="button" class="generate-button" :disabled="app.generating" @click="generateSelected">
            <LoaderCircle v-if="app.generating" class="spin" :size="16" stroke-width="1.7" aria-hidden="true" />
            <Sparkles v-else :size="16" stroke-width="1.7" aria-hidden="true" />
            <span>{{ app.generating ? '正在生成…' : '生成学习文档' }}</span>
          </button>
        </header>

        <div v-if="app.error" class="annotation-error" role="alert">
          <CircleAlert :size="16" stroke-width="1.5" aria-hidden="true" />
          <span>{{ app.error }}</span>
        </div>
        <div v-else-if="app.loading || app.generating" class="annotation-loading" role="status">
          <LoaderCircle class="spin" :size="16" stroke-width="1.5" aria-hidden="true" />
          <span>{{ app.generating ? '正在生成学习文档，完成后会自动打开。' : '正在打开已保存的学习文档…' }}</span>
        </div>
        <div v-else-if="!selectedBook" class="library-welcome" role="status">
          <p class="library-label">从这里继续</p>
          <h2>选择一本教材</h2>
          <p>左侧列出已保存的教材和学习文档。选择文档即可直接阅读，不会重新调用模型。</p>
        </div>
        <div v-else-if="blocked" class="annotation-empty" role="alert">
          此部分暂不可用，学习内容正在等待进一步核查。
        </div>
        <div v-else-if="!document" class="library-welcome" role="status">
          <p class="library-label">{{ selectedBookDocuments.length ? '选择学习文档' : '教材已保存' }}</p>
          <h2>{{ selectedBookDocuments.length ? '从已有版本继续' : '准备生成学习页面' }}</h2>
          <p>{{ selectedBookDocuments.length ? '选择左侧的文档版本，打开已保存的页面。' : '点击右上角“生成学习文档”开始一次新的生成运行。' }}</p>
        </div>
        <template v-else>
          <div v-if="atRisk" class="review-notice" role="status">
            <CircleAlert :size="16" stroke-width="1.6" aria-hidden="true" />
            <span>这份学习文档包含待核查提示，阅读时请结合教材依据判断。</span>
          </div>
          <LearningDocumentRenderer :document="document" />
        </template>
      </section>
    </section>
  </div>
</template>

<style scoped>
.annotation-masthead { display: flex; align-items: flex-start; justify-content: space-between; gap: 2rem; }
.annotation-title-block { max-width: 46rem; }
.annotation-kicker { margin: 0 0 .85rem; color: #8b2e24; font: 600 .7rem/1.2 "IBM Plex Mono", "SFMono-Regular", Consolas, monospace; letter-spacing: .18em; }
.annotation-title-row { display: flex; align-items: center; gap: .7rem; }
.annotation-title-row svg { color: #8b2e24; flex: 0 0 auto; }
h1 { margin: 0; color: #171717; font: 600 clamp(2rem, 4vw, 3.2rem)/1.12 "Noto Serif SC", "Source Han Serif SC", "Songti SC", serif; letter-spacing: -.03em; }
.annotation-dek { max-width: 38rem; margin: 1rem 0 0; color: #55514b; font-size: 1rem; line-height: 1.8; }
.annotation-rule { height: 1px; margin: 2rem 0 2.5rem; background: #c9c5bd; }
.library-workspace { display: grid; grid-template-columns: minmax(15rem, 18rem) minmax(0, 1fr); gap: clamp(2rem, 5vw, 5rem); align-items: start; }
.library-sidebar { min-width: 0; }
.library-sidebar-heading, .library-content-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
.library-sidebar-heading { padding-bottom: .9rem; border-bottom: 1px solid #c9c5bd; }
.library-label { margin: 0; color: #8b2e24; font: 600 .66rem/1.3 "IBM Plex Mono", "SFMono-Regular", Consolas, monospace; letter-spacing: .12em; text-transform: uppercase; }
.library-sidebar h2, .library-content h2 { margin: .55rem 0 0; color: #171717; font: 600 1.35rem/1.25 "Noto Serif SC", "Source Han Serif SC", "Songti SC", serif; }
.upload-control { display: inline-flex; align-items: center; gap: .4rem; padding: .45rem .6rem; border: 1px solid #a9a49b; color: #55514b; font: .7rem/1.2 "IBM Plex Mono", monospace; cursor: pointer; transition: border-color .15s ease, color .15s ease; }
.upload-control:hover { border-color: #8b2e24; color: #8b2e24; }
.upload-control.is-disabled { cursor: progress; opacity: .6; }
.upload-control input { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }
.book-list, .document-list { margin: 0; padding: 0; list-style: none; }
.book-list { border-bottom: 1px solid #c9c5bd; }
.book-list-item { border-bottom: 1px solid #dedad2; }
.book-list-item:last-child { border-bottom: 0; }
.book-item, .document-item { width: 100%; border: 0; background: transparent; text-align: left; cursor: pointer; }
.book-item { display: grid; grid-template-columns: 1.7rem minmax(0, 1fr) auto; gap: .65rem; align-items: start; padding: .9rem 0; color: #55514b; }
.book-item:hover, .book-item.is-selected { color: #8b2e24; }
.book-item-icon { display: grid; place-items: center; width: 1.7rem; height: 1.7rem; border: 1px solid #c9c5bd; color: #8b2e24; }
.book-item-copy { display: grid; gap: .28rem; min-width: 0; }
.book-item-copy strong { overflow: hidden; color: inherit; font-size: .85rem; font-weight: 600; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
.book-item-copy small, .book-item time { color: #88837b; font: .66rem/1.4 "IBM Plex Mono", monospace; }
.book-item time { padding-top: .15rem; white-space: nowrap; }
.document-list { margin: 0 0 .65rem 2.35rem; border-left: 1px solid #c9c5bd; }
.document-item { display: grid; grid-template-columns: 1rem minmax(0, 1fr); gap: .45rem; align-items: center; padding: .48rem .5rem; color: #77716a; font-size: .76rem; line-height: 1.4; }
.document-item span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.document-item small { grid-column: 2; color: #9a948c; font: .61rem/1.3 "IBM Plex Mono", monospace; }
.document-item:hover, .document-item.is-selected { background: #f1eee8; color: #8b2e24; }
.document-item small.status-blocked { color: #8b2e24; }
.document-item small.status-at_risk { color: #a45a22; }
.document-item small.status-passed, .document-item small.status-published, .document-item small.status-accepted { color: #52735f; }
.library-status { display: flex; align-items: center; gap: .5rem; padding: 1rem 0; border-bottom: 1px solid #c9c5bd; color: #77716a; font-size: .8rem; }
.library-status-error { color: #8b2e24; }
.library-empty { display: grid; gap: .55rem; padding: 1.25rem 0; border-bottom: 1px solid #c9c5bd; color: #77716a; }
.library-empty svg { color: #8b2e24; }
.library-empty p { margin: 0; color: #55514b; font-size: .85rem; }
.library-empty span { color: #88837b; font-size: .75rem; line-height: 1.6; }
.library-content { min-width: 0; }
.library-content-heading { padding-bottom: 1.25rem; border-bottom: 1px solid #c9c5bd; }
.library-content-meta { margin: .45rem 0 0; color: #88837b; font: .7rem/1.4 "IBM Plex Mono", monospace; }
.generate-button { display: inline-flex; align-items: center; gap: .45rem; flex: 0 0 auto; min-height: 2.25rem; padding: .55rem .7rem; border: 1px solid #8b2e24; background: #8b2e24; color: #fffdf8; font: 600 .72rem/1.2 "IBM Plex Mono", monospace; cursor: pointer; transition: background .15s ease, border-color .15s ease; }
.generate-button:hover:not(:disabled) { border-color: #6e211a; background: #6e211a; }
.generate-button:disabled { cursor: progress; opacity: .7; }
.annotation-error, .annotation-loading { display: flex; align-items: center; gap: .55rem; padding: 1rem 0; border-top: 1px solid #c9c5bd; border-bottom: 1px solid #c9c5bd; color: #55514b; font-size: .9rem; }
.annotation-error { color: #8b2e24; }
.annotation-empty { padding: 1rem 0; border-top: 1px solid #c9c5bd; border-bottom: 1px solid #c9c5bd; color: #55514b; font-size: .9rem; }
.library-welcome { max-width: 34rem; padding: 4rem 0; }
.library-welcome h2 { margin-top: .8rem; font-size: 2rem; }
.library-welcome p:last-child { max-width: 30rem; margin: 1rem 0 0; color: #77716a; font-size: .9rem; line-height: 1.8; }
.review-notice { display: flex; align-items: flex-start; gap: .55rem; margin-bottom: 1.5rem; padding: .8rem .9rem; border-left: 2px solid #a45a22; background: #fbf3e7; color: #7b522d; font-size: .78rem; line-height: 1.6; }
.review-notice svg { flex: 0 0 auto; margin-top: .1rem; }
.spin { animation: annotation-spin .9s linear infinite; }
@keyframes annotation-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
@media (max-width: 760px) { .library-workspace { display: block; } .library-sidebar { margin-bottom: 2.5rem; } .library-content-heading { display: block; } .generate-button { margin-top: 1rem; } }
@media (max-width: 480px) { .annotation-masthead { display: block; } .annotation-title-row { align-items: flex-start; } .annotation-title-row svg { margin-top: .35rem; } .book-item { grid-template-columns: 1.7rem minmax(0, 1fr); } .book-item time { display: none; } }
</style>
