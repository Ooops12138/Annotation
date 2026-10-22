import { afterEach, describe, expect, it, vi } from 'vitest'
import { getDocument, getLibrary, startRun, uploadBook } from './api'

function response(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('document library API boundary', () => {
  it('reads the library with GET and normalizes nested repository records', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({
      status: 'ok',
      books: [{ book_id: 'book-1', title: '教材', documents: [{ document_id: 'doc-1', title: '第一章', current_version: { version: 2, review_status: 'at_risk' } }] }],
    }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await getLibrary()

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/api/library', undefined)
    expect(result.books[0].documents?.[0].review_status).toBe('at_risk')
  })

  it('reads a saved document without changing the request method', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ status: 'ok', document: { document_id: 'doc/a' }, review_report: null }))
    vi.stubGlobal('fetch', fetchMock)

    await getDocument('doc/a')

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/api/documents/doc%2Fa', undefined)
  })

  it('uploads a PDF as multipart form data', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ status: 'ok', book: { book_id: 'book-2', title: '新教材' } }))
    vi.stubGlobal('fetch', fetchMock)
    const file = new Blob(['pdf bytes'], { type: 'application/pdf' })

    await uploadBook(file, 'new-book.pdf')

    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    expect((init.body as FormData).get('file')).toBeTruthy()
  })

  it('starts a run only through the explicit runs endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ status: 'ok', run_id: 'run-1' }))
    vi.stubGlobal('fetch', fetchMock)

    await startRun('book-1')

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/api/runs', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ book_id: 'book-1' }),
    }))
  })
  it.each([
    { data: { books: [] } },
    { library: { books: [] } },
    { documents: [] },
    { books: [{ book_id: 'book-1', documents: [{ document_id: 'doc-1', current_version: null }] }] },
  ])('rejects retired library shapes and invalid records', async (payload) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(payload)))
    await expect(getLibrary()).rejects.toThrow()
  })

  it('uses nested repository versions when a duplicate flat index conflicts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({
      books: [{ book_id: 'book-1', documents: [{ document_id: 'doc-1', title: '章节', current_version: { review_status: 'blocked' } }] }],
      documents: [{ document_id: 'doc-1', title: '旧索引', review_status: 'passed' }],
    })))
    const result = await getLibrary()
    expect(result.documents).toHaveLength(1)
    expect(result.documents[0].review_status).toBe('blocked')
  })

  it('keeps a blocked document response explicit even without an audit report', async () => {
    const payload = { status: 'blocked', document: null, review_report: null }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(payload)))
    expect(await getDocument('doc-1')).toEqual(payload)
  })

  it('rejects a retired document envelope', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ data: { document: { document_id: 'doc-1' } } })))
    await expect(getDocument('doc-1')).rejects.toThrow('文档响应格式无效')
  })

})
