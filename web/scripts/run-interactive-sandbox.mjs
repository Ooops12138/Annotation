import { readFile, writeFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { chromium } from '@playwright/test'

const [, , inputPath, reportPath] = process.argv
if (!inputPath || !reportPath) {
  throw new Error('expected input and report paths')
}

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const webRoot = path.resolve(scriptDirectory, '..')
const started = Date.now()
let server
let browser
let report = {
  status: 'browser_error',
  console_errors: [],
  dom_snapshot: '',
  accessibility_snapshot: '',
  action_results: [],
  screenshot_path: null,
  network_blocked: false,
  blocked_requests: [],
  error: null,
  duration_ms: null,
}

function stringSnapshot(value) {
  return String(value || '').slice(0, 20000)
}

function safeComponentId(value) {
  return String(value || 'component').replace(/[^A-Za-z0-9_-]+/g, '-').slice(0, 80) || 'component'
}

function systemChromiumCandidates() {
  if (process.platform === 'win32') {
    return [
      process.env.ANNOTATION_CHROMIUM_EXECUTABLE,
      process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
      process.env.CHROME_EXECUTABLE,
      process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
      process.env['ProgramFiles(x86)'] && path.join(process.env['ProgramFiles(x86)'], 'Google', 'Chrome', 'Application', 'chrome.exe'),
      process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Google', 'Chrome', 'Application', 'chrome.exe'),
      process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
      process.env['ProgramFiles(x86)'] && path.join(process.env['ProgramFiles(x86)'], 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    ].filter(Boolean)
  }
  if (process.platform === 'darwin') {
    return [
      process.env.ANNOTATION_CHROMIUM_EXECUTABLE,
      process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
      process.env.CHROME_EXECUTABLE,
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
      '/Applications/Chromium.app/Contents/MacOS/Chromium',
    ].filter(Boolean)
  }
  return [
    process.env.ANNOTATION_CHROMIUM_EXECUTABLE,
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    process.env.CHROME_EXECUTABLE,
    '/usr/bin/google-chrome-stable',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
    '/usr/bin/microsoft-edge',
  ].filter(Boolean)
}

async function launchChromium() {
  try {
    return await chromium.launch()
  } catch (error) {
    const firstError = error instanceof Error ? error.message : String(error)
    const { access } = await import('node:fs/promises')
    for (const candidate of systemChromiumCandidates()) {
      try {
        await access(candidate)
        return await chromium.launch({ executablePath: candidate })
      } catch {}
    }
    throw new Error(`${firstError}\nNo usable system Chrome, Edge, or Chromium executable was found for the sandbox fallback.`)
  }
}

try {
  const input = JSON.parse(await readFile(inputPath, 'utf8'))
  const { spec, timeout_ms: timeoutMs = 10000, output_dir: outputDirectory } = input
  await mkdir(outputDirectory, { recursive: true })
  server = await createServer({
    root: webRoot,
    configFile: false,
    logLevel: 'error',
    server: { host: '127.0.0.1', port: 0, strictPort: false },
  })
  await server.listen()
  const address = server.httpServer?.address()
  const port = typeof address === 'object' && address ? address.port : 5173
  const origin = `http://127.0.0.1:${port}`
  browser = await launchChromium()
  const context = await browser.newContext({ viewport: { width: 1080, height: 760 } })
  const page = await context.newPage()
  page.setDefaultTimeout(timeoutMs)
  page.on('console', (message) => {
    const text = stringSnapshot(message.text())
    if (message.type() === 'error' && !text.includes('ERR_BLOCKED_BY_CLIENT')) report.console_errors.push(text)
  })
  page.on('pageerror', (error) => report.console_errors.push(stringSnapshot(error.message)))
  await context.route('**/*', async (route) => {
    const url = route.request().url()
    if (url.startsWith(origin)) return route.continue()
    report.blocked_requests.push(url)
    return route.abort('blockedbyclient')
  })
  await page.addInitScript((componentSpec) => {
    window.__ANNOTATION_COMPONENT_SPEC__ = componentSpec
  }, spec)
  await page.goto(`${origin}/interactive-sandbox.html`, { waitUntil: 'domcontentloaded', timeout: timeoutMs })
  await page.waitForFunction(() => window.__annotationInteractiveSandbox?.ready === true, null, { timeout: timeoutMs })
  const root = page.locator('#sandbox-root')
  const networkBlocked = await page.evaluate(async () => {
    try {
      await fetch('https://annotation-sandbox.invalid/network-check')
      return false
    } catch {
      return true
    }
  })
  report.network_blocked = networkBlocked
  if (!networkBlocked) throw new Error('external network request was not blocked')

  for (const action of spec.test_actions || []) {
    const control = page.locator(`[data-component-control="${action.control_id}"]`)
    let passed = false
    let detail = ''
    try {
      if (action.action === 'toggle') {
        if (action.value) await control.check()
        else await control.uncheck()
      } else if (action.action === 'set_range') {
        await control.evaluate((input, value) => {
          input.value = String(value)
          input.dispatchEvent(new Event('input', { bubbles: true }))
        }, action.value)
      } else {
        throw new Error(`unsupported test action: ${action.action}`)
      }
      await page.waitForFunction((expected) => document.querySelector('#interactive-observation')?.textContent?.includes(expected), action.expected_text, { timeout: timeoutMs })
      passed = true
    } catch (error) {
      detail = error instanceof Error ? error.message : String(error)
    }
    report.action_results.push({ action: action.action, control_id: action.control_id, passed, detail })
  }
  report.dom_snapshot = stringSnapshot(await root.evaluate((element) => element.outerHTML))
  try {
    report.accessibility_snapshot = stringSnapshot(await root.ariaSnapshot())
  } catch {
    report.accessibility_snapshot = stringSnapshot(await root.evaluate((element) => {
      const collect = (node) => Array.from(node.children).map((child) => ({
        role: child.getAttribute('role'),
        label: child.getAttribute('aria-label'),
        text: child.textContent?.trim().slice(0, 180),
        children: collect(child),
      }))
      return JSON.stringify({ role: element.getAttribute('role'), label: element.getAttribute('aria-label'), children: collect(element) })
    }))
  }
  const screenshotPath = path.join(outputDirectory, `sandbox-${safeComponentId(spec.component_id)}-${Date.now()}.png`)
  await page.screenshot({ path: screenshotPath, fullPage: true })
  report.screenshot_path = screenshotPath
  if (report.console_errors.length) report.status = 'render_error'
  else if (report.action_results.some((item) => !item.passed)) report.status = 'assertion_failed'
  else report.status = 'passed'
  await context.close()
} catch (error) {
  const message = error instanceof Error ? error.message : String(error)
  report.error = stringSnapshot(message)
  if (/Timeout|timeout/i.test(message)) report.status = 'timeout'
  else if (/render|invalid interactive component|restricted math/i.test(message)) report.status = 'render_error'
  else report.status = 'browser_error'
} finally {
  report.duration_ms = Date.now() - started
  try {
    if (browser) await browser.close()
  } catch {}
  try {
    if (server) await server.close()
  } catch {}
  await writeFile(reportPath, JSON.stringify(report, null, 2), 'utf8')
}
