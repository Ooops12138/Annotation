import { componentLibraryMarkup } from './interactiveLibraries'
import { isInteractiveComponentSpec } from './document'
import type { GeneratedHtmlSpec, InteractiveComponentSpec } from './document'

export interface InteractiveComponentRenderHandle { destroy: () => void; ready: Promise<void> }

async function generatedDocument(spec: GeneratedHtmlSpec): Promise<string> {
  // Code fields are validated as fragments in both the API contract and this
  // client boundary. The renderer, rather than generated code, owns CSP and
  // sandbox permissions.
  const escapeScript = (source: string): string => source.replace(/<\/script/gi, '<\\/script')
  const libraryRuntime = await componentLibraryMarkup(spec.libraries || [])
  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; connect-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>${spec.css}</style></head><body>${spec.html}${libraryRuntime}<script>${escapeScript(spec.javascript)}</script></body></html>`
}

function renderGeneratedHtml(host: HTMLElement, spec: GeneratedHtmlSpec): InteractiveComponentRenderHandle {
  const frame = document.createElement('iframe')
  frame.className = 'interactive-component-frame'
  frame.title = spec.accessibility.aria_label
  frame.setAttribute('sandbox', 'allow-scripts')
  frame.referrerPolicy = 'no-referrer'
  let destroyed = false
  const ready = generatedDocument(spec).then((source) => {
    if (destroyed) return
    frame.srcdoc = source
    host.append(frame)
    host.dataset.componentReady = 'true'
  })
  return {
    ready,
    destroy: () => {
      destroyed = true
      frame.remove()
      host.dataset.componentReady = 'false'
    },
  }
}

export function renderInteractiveComponent(host: HTMLElement, spec: InteractiveComponentSpec): InteractiveComponentRenderHandle {
  if (!isInteractiveComponentSpec(spec)) throw new Error('交互组件代码包不符合契约。')
  host.replaceChildren()
  host.classList.add('interactive-component-host')
  host.dataset.componentType = spec.component_type
  host.dataset.componentReady = 'false'
  host.setAttribute('aria-label', spec.accessibility.aria_label)
  return renderGeneratedHtml(host, spec)
}
