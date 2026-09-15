<script setup lang="ts">
import { renderMarkdown, renderMarkdownInline } from '../../lib/markdown'

withDefaults(defineProps<{ tone?: 'info' | 'warning' | 'success'; title: string; content: string }>(), { tone: 'info' })
</script>

<template>
  <aside class="annotation-callout" :data-tone="tone">
    <p class="annotation-node-label">{{ tone === 'warning' ? '建议核实' : tone === 'success' ? '重点' : '学习提示' }}</p>
    <h4><span class="annotation-rich-text-inline" v-html="renderMarkdownInline(title)"></span></h4>
    <div class="annotation-rich-text" v-html="renderMarkdown(content)"></div>
  </aside>
</template>
