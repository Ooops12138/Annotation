const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

export async function getHealth(): Promise<{ status: string; service: string; version: string }> {
  const response = await fetch(`${baseUrl}/health`)
  if (!response.ok) throw new Error(`健康检查失败 (${response.status})`)
  return response.json()
}

export async function getDemoDocument(): Promise<any> {
  const response = await fetch(`${baseUrl}/api/demo-document`)
  if (!response.ok) throw new Error(`文档加载失败 (${response.status})`)
  return response.json()
}
