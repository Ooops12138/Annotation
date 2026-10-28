import { afterEach, describe, expect, it, vi } from 'vitest'
import type { GeneratedHtmlSpec } from './document'

const { load } = vi.hoisted(() => ({ load: vi.fn() }))
vi.mock('./interactiveLibraries', () => ({ componentLibraryMarkup: load }))
import { renderInteractiveComponent } from './interactiveRenderer'

const spec: GeneratedHtmlSpec = {
  component_type: 'generated_html', component_id: 'test', title: 'Observe', learning_objective: 'Compare',
  source_refs: ['src-1'], accessibility: { aria_label: 'Compare', description: 'States', observation: 'Observe' },
  controls: [], test_actions: [], annotations: [], libraries: ['d3'], html: '<div>Diagram</div>', css: '', javascript: 'd3.select("div")',
}
function setup() {
  const frame = { setAttribute: vi.fn(), remove: vi.fn(), srcdoc: '' }
  vi.stubGlobal('document', { createElement: () => frame })
  const host = { replaceChildren: vi.fn(), classList: { add: vi.fn() }, dataset: {}, setAttribute: vi.fn(), append: vi.fn() }
  return { frame, host, element: host as unknown as HTMLElement }
}
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

describe('asynchronous component runtime', () => {
  it('injects libraries before generated code and preserves the isolation boundary', async () => {
    const { frame, host, element } = setup()
    load.mockResolvedValue('<script>/* library */</script>')
    const handle = renderInteractiveComponent(element, spec)
    expect(host.append).not.toHaveBeenCalled()
    await handle.ready
    expect(load).toHaveBeenCalledWith(['d3'])
    expect(frame.setAttribute).toHaveBeenCalledWith('sandbox', 'allow-scripts')
    expect(frame.srcdoc).toContain("connect-src 'none'")
    expect(frame.srcdoc.indexOf('/* library */')).toBeLessThan(frame.srcdoc.indexOf('d3.select'))
    expect(host.append).toHaveBeenCalledWith(frame)
  })
  it('does not mount a component destroyed during library loading', async () => {
    const { host, element } = setup()
    let finish!: (value: string) => void
    load.mockReturnValue(new Promise<string>((resolve) => { finish = resolve }))
    const handle = renderInteractiveComponent(element, spec)
    handle.destroy()
    finish('library')
    await handle.ready
    expect(host.append).not.toHaveBeenCalled()
  })
  it('surfaces runtime loading failures to the page and sandbox', async () => {
    const { host, element } = setup()
    load.mockRejectedValue(new Error('library unavailable'))
    await expect(renderInteractiveComponent(element, spec).ready).rejects.toThrow('library unavailable')
    expect(host.append).not.toHaveBeenCalled()
  })
})
