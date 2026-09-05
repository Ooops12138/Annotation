import { defineStore } from 'pinia'
import { getDemoDocument, getHealth } from '../lib/api'

export const useAppStore = defineStore('app', {
  state: () => ({ health: 'checking', document: null as any, error: '' }),
  actions: {
    async load() {
      try {
        const [health, document] = await Promise.all([getHealth(), getDemoDocument()])
        this.health = health.status
        this.document = document
      } catch (error) {
        this.health = 'offline'
        this.error = error instanceof Error ? error.message : '未知错误'
      }
    }
  }
})
