<script setup lang="ts">
const props = defineProps<{ sourceRefs?: string[] }>()

function learnerSourceLabel(sourceRef: string, index: number): string {
  const page = sourceRef.match(/(?:^|-)p(\d+)(?:-|$)/i)?.[1]
  const block = sourceRef.match(/(?:^|-)b(\d+)(?:-|$)/i)?.[1]
  if (page && block) return `教材依据 · 第 ${page} 页 · 第 ${block} 段`
  if (page) return `教材依据 · 第 ${page} 页`
  return `教材依据 · 第 ${index + 1} 条（页码待确认）`
}
</script>

<template>
  <details v-if="sourceRefs?.length" class="annotation-source-panel">
    <summary>展开教材依据 <span>（{{ sourceRefs.length }} 条）</span></summary>
    <ol>
      <li v-for="(sourceRef, index) in props.sourceRefs" :key="`${sourceRef}-${index}`">{{ learnerSourceLabel(sourceRef, index) }}</li>
    </ol>
  </details>
</template>
