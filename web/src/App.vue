<script setup lang="ts">
import { onMounted } from 'vue'
import { Activity, BookOpen, CircleAlert, Sparkles } from 'lucide-vue-next'
import LearningDocumentRenderer from './components/domain/LearningDocumentRenderer.vue'
import { useAppStore } from './stores/app'

const app = useAppStore()
onMounted(() => app.load())
</script>

<template>
  <div class="min-h-screen bg-paper text-ink">
    <header class="border-b border-slate-200 bg-white/80 backdrop-blur">
      <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-5"><div class="flex items-center gap-3"><div class="grid h-10 w-10 place-items-center rounded-xl bg-slate-950 text-emerald-200"><BookOpen :size="19" /></div><div><p class="font-display text-xl">Annotation</p><p class="text-xs tracking-wide text-slate-400">教材驱动的学习文档</p></div></div><div class="flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1.5 text-xs"><Activity :size="14" :class="app.health === 'ok' ? 'text-moss' : 'text-signal'" /> API {{ app.health }}</div></div>
    </header>
    <main class="mx-auto grid max-w-6xl gap-10 px-6 py-12 lg:grid-cols-[240px_1fr]">
      <aside class="space-y-8"><div><p class="text-xs font-semibold uppercase tracking-[0.2em] text-signal">PDF → BLUEPRINT → IR</p><h1 class="mt-4 font-display text-4xl leading-tight">从教材到可学习页面</h1><p class="mt-4 text-sm leading-6 text-slate-500">运行真实的教材导入、蓝图生成、文档生成和基础审核闭环。</p></div><div class="rounded-2xl bg-slate-950 p-5 text-sm text-slate-300"><div class="flex items-center gap-2 text-emerald-200"><Sparkles :size="15" /> 运行状态</div><p class="mt-3 leading-6">{{ app.loading ? '正在运行工作流…' : `${app.run?.provider_metadata?.provider ?? '—'} · ${app.run?.source_block_count ?? 0} 个教材片段` }}</p><p v-if="app.run?.document" class="mt-2 text-xs text-slate-400">文档状态：{{ app.run.document.status }} · 蓝图单元：{{ app.blueprint?.knowledge_units?.length ?? 0 }}</p></div></aside>
      <div><div v-if="app.error" class="mb-6 flex items-center gap-2 rounded-xl bg-red-50 p-4 text-sm text-red-700"><CircleAlert :size="16" />{{ app.error }}</div><div v-if="app.loading" class="rounded-2xl border border-slate-200 bg-white p-8 text-slate-500">正在读取教材并生成学习文档…</div><LearningDocumentRenderer v-else :document="app.document" /></div>
    </main>
  </div>
</template>
