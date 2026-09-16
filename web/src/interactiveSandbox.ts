import { isInteractiveComponentSpec } from './lib/document'
import { renderInteractiveComponent } from './lib/interactiveRenderer'
import './style.css'

declare global {
  interface Window {
    __ANNOTATION_COMPONENT_SPEC__?: unknown
    __annotationInteractiveSandbox?: { ready: boolean; error?: string }
  }
}

const root = document.querySelector<HTMLElement>('#sandbox-root')

try {
  if (!root) throw new Error('sandbox root is unavailable')
  const spec = window.__ANNOTATION_COMPONENT_SPEC__
  if (!isInteractiveComponentSpec(spec)) throw new Error('sandbox received an invalid interactive component spec')
  renderInteractiveComponent(root, spec)
  window.__annotationInteractiveSandbox = { ready: true }
} catch (error) {
  const message = error instanceof Error ? error.message : String(error)
  if (root) {
    root.replaceChildren()
    const alert = document.createElement('p')
    alert.setAttribute('role', 'alert')
    alert.textContent = '互动图示暂不可用。'
    root.append(alert)
  }
  window.__annotationInteractiveSandbox = { ready: false, error: message }
  console.error('[Annotation sandbox] interactive component render failed:', error)
}
