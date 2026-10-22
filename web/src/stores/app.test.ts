import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import {
  getDocument,
  getLibrary,
  startRun,
  uploadBook,
} from '../lib/api'
import { useAppStore } from './app'

vi.mock('../lib/api', () => ({
  getDocument: vi.fn(),
  getLibrary: vi.fn(),
  startRun: vi.fn(),
  uploadBook: vi.fn(),
}))

const library = {
  books: [{ book_id: 'book-1', title: '高等数学', documents: [{ document_id: 'doc-1', book_id: 'book-1', title: '极限', review_status: 'passed' }] }],
  documents: [{ document_id: 'doc-1', book_id: 'book-1', title: '极限', review_status: 'passed' }],
}

const document = {
  artifact_id: 'artifact-1',
  document_id: 'doc-1',
  run_id: 'run-1',
  version: 1,
  status: 'published',
  created_by: 'provider:mock',
  blueprint_version: 'bp-1:v1',
  title: '极限',
  sections: [],
}

afterEach(() => {
  vi.clearAllMocks()
})

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('document library store', () => {
  it('loads the library without starting a workflow', async () => {
    vi.mocked(getLibrary).mockResolvedValue(library)
    const store = useAppStore()

    await store.loadLibrary()

    expect(getLibrary).toHaveBeenCalledTimes(1)
    expect(startRun).not.toHaveBeenCalled()
    expect(store.books[0].book_id).toBe('book-1')
    expect(store.selectedBookId).toBe('book-1')
    expect(store.selectedDocumentId).toBeNull()
  })

  it('does not load a document merely because a textbook is selected', async () => {
    vi.mocked(getLibrary).mockResolvedValue(library)
    const store = useAppStore()
    await store.loadLibrary()

    await store.selectBook('book-1')

    expect(getDocument).not.toHaveBeenCalled()
    expect(startRun).not.toHaveBeenCalled()
  })

  it('fetches a saved Document IR only after the user selects it', async () => {
    vi.mocked(getLibrary).mockResolvedValue(library)
    vi.mocked(getDocument).mockResolvedValue({ status: 'ok', document, review_report: null })
    const store = useAppStore()
    await store.loadLibrary()

    await store.selectDocument('doc-1')

    expect(getDocument).toHaveBeenCalledWith('doc-1')
    expect(store.document).toEqual(document)
    expect(store.selectedDocumentId).toBe('doc-1')
  })

  it('uploads a PDF and selects the returned textbook without using the model', async () => {
    vi.mocked(getLibrary).mockResolvedValue({ books: [], documents: [] })
    vi.mocked(uploadBook).mockResolvedValue({ book_id: 'book-2', title: '线性代数', documents: [] })
    const store = useAppStore()

    await store.addBook({} as Blob, 'linear-algebra.pdf')

    expect(uploadBook).toHaveBeenCalledWith(expect.anything(), 'linear-algebra.pdf')
    expect(startRun).not.toHaveBeenCalled()
    expect(store.selectedBookId).toBe('book-2')
  })

  it('starts generation only from the explicit generate action', async () => {
    vi.mocked(getLibrary).mockResolvedValue(library)
    vi.mocked(startRun).mockResolvedValue({ status: 'succeeded', run_id: 'run-2', book_id: 'book-1', document })
    const store = useAppStore()
    await store.selectBook('book-1')

    await store.generate()

    expect(startRun).toHaveBeenCalledWith('book-1')
    expect(store.document).toEqual(document)
    expect(store.selectedDocumentId).toBe('doc-1')
  })
  it('clears a previously open document when a new run is blocked', async () => {
    vi.mocked(getLibrary).mockResolvedValue(library)
    vi.mocked(startRun).mockResolvedValue({ status: 'blocked', document: null })
    const store = useAppStore()
    store.document = document
    await store.generate('book-1')
    expect(store.document).toBeNull()
    expect(store.error).toContain('阻塞')
    expect(getDocument).not.toHaveBeenCalled()
  })

  it('rejects a successful run with only an ID instead of guessing another fetch', async () => {
    vi.mocked(startRun).mockResolvedValue({ status: 'ok', document_id: 'doc-1' })
    const store = useAppStore()
    await expect(store.generate('book-1')).rejects.toThrow('运行响应缺少学习文档')
    expect(getDocument).not.toHaveBeenCalled()
  })

  it('does not show stale content when a saved document is blocked', async () => {
    vi.mocked(getDocument).mockResolvedValue({ status: 'blocked', document: null, review_report: null })
    const store = useAppStore()
    store.document = document
    await store.selectDocument('doc-1')
    expect(store.document).toBeNull()
    expect(store.error).toContain('暂不可用')
  })

})
