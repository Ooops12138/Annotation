import { defineStore } from 'pinia'
import { getHealth, runWorkflow } from '../lib/api'

export const useAppStore = defineStore('app', {
  state: () => ({ health: 'checking', document: null as any, blueprint: null as any, run: null as any, loading: false, error: '' }),
  actions: {
    async load() {
      try {
        this.loading = true
        const [health, result] = await Promise.all([getHealth(), runWorkflow()])
        this.health = health.status
        this.document = result.document
        this.blueprint = result.blueprint
        this.run = result
      } catch (error) {
        this.health = 'offline'
        this.error = error instanceof Error ? error.message : '未知错误'
      } finally {
        this.loading = false
      }
    }
  }
})
