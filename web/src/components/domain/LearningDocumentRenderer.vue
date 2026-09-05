<script setup lang="ts">
defineProps<{ document: any }>()
</script>

<template>
  <article v-if="document" class="space-y-10">
    <section v-for="section in document.sections" :key="section.id" class="space-y-5">
      <div class="flex items-center gap-3"><span class="h-px w-8 bg-signal"></span><p class="text-xs font-semibold uppercase tracking-[0.22em] text-signal">章节</p></div>
      <h2 class="font-display text-3xl text-ink">{{ section.title }}</h2>
      <div v-for="node in section.children" :key="node.id" class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <p v-if="node.type === 'markdown'" class="leading-8 text-slate-700">{{ node.content }}</p>
        <div v-else-if="node.type === 'formula'" class="overflow-x-auto rounded-xl bg-slate-950 px-6 py-5 text-center font-mono text-lg text-emerald-200">{{ node.latex }}</div>
        <div v-else-if="node.type === 'callout'" class="border-l-4 border-moss bg-emerald-50 p-4"><p class="font-semibold text-emerald-900">{{ node.title }}</p><p class="mt-1 text-emerald-800">{{ node.content }}</p></div>
        <div v-else-if="node.type === 'quiz'" class="space-y-4"><div><p class="text-xs font-semibold uppercase tracking-widest text-slate-400">练习</p><p class="mt-2 text-lg font-medium text-ink">{{ node.question }}</p></div><div class="grid gap-2 sm:grid-cols-3"><button v-for="option in node.options" :key="option" class="rounded-xl border border-slate-200 px-3 py-2 text-left text-sm transition hover:border-signal hover:bg-orange-50">{{ option }}</button></div></div>
        <p v-else class="text-sm text-red-600">不支持的节点类型：{{ node.type }}</p>
        <p v-if="node.source_refs?.length" class="mt-4 text-xs text-slate-400">来源：{{ node.source_refs.join(' · ') }}</p>
      </div>
    </section>
  </article>
</template>
