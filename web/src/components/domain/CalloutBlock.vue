<script setup lang="ts">
withDefaults(defineProps<{ tone?: 'info' | 'warning' | 'success'; title: string; content: string }>(), { tone: 'info' })
</script>

<template>
  <aside class="annotation-callout" :data-tone="tone">
    <p class="annotation-node-label">{{ tone === 'warning' ? '建议核实' : tone === 'success' ? '重点' : '学习提示' }}</p>
    <h4>{{ title }}</h4>
    <p>{{ content }}</p>
  </aside>
</template>
