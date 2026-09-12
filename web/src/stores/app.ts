import { defineStore } from 'pinia'
import { runWorkflow } from '../lib/api'

export const useAppStore = defineStore('app', {
  state: () => ({ document: null as any, blueprint: null as any, reviewReport: null as any, run: null as any, loading: false, error: '' }),
  actions: {
    async load() {
      try {
        this.loading = true
        const result = await runWorkflow()
        this.document = result.document
        this.blueprint = result.blueprint
        this.reviewReport = result.review_report
        this.run = result
      } catch (error) {
        this.error = error instanceof Error ? error.message : '未知错误'
      } finally {
        this.loading = false
      }
    }
  }
})
