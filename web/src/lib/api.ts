const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()
const baseUrl = (configuredBaseUrl || 'http://127.0.0.1:8000').replace(/\/$/, '')

async function fetchApi(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${baseUrl}${path}`, init)
  } catch (error) {
    const detail = error instanceof Error ? error.message : '网络连接失败'
    throw new Error(`无法连接后端 ${baseUrl}（${detail}）。请确认 API 已启动。`)
  }
}

export async function getHealth(): Promise<{ status: string; service: string; version: string }> {
  const response = await fetchApi('/health')
  if (!response.ok) throw new Error(`健康检查失败 (${response.status})`)
  return response.json()
}

export async function getDemoDocument(): Promise<any> {
  const response = await fetchApi('/api/demo-document')
  if (!response.ok) throw new Error(`文档加载失败 (${response.status})`)
  return response.json()
}

export async function runWorkflow(): Promise<any> {
  const response = await fetchApi('/api/workflow/run', { method: 'POST' })
  if (!response.ok) throw new Error(`工作流运行失败 (${response.status})`)
  const payload = await response.json()
  if (payload.status === 'error') throw new Error(payload.errors?.join('；') ?? '工作流运行失败')
  return payload
}
