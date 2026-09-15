<script setup lang="ts">
import { renderMarkdown, renderMarkdownInline } from '../../lib/markdown'

defineProps<{ title: string; problem: string; solution: string }>()
</script>

<template>
  <section class="annotation-example" aria-label="例题">
    <p class="annotation-node-label">例题</p>
    <h4><span class="annotation-rich-text-inline" v-html="renderMarkdownInline(title)"></span></h4>
    <div class="annotation-example-problem annotation-rich-text" v-html="renderMarkdown(problem)"></div>
    <div class="annotation-example-solution">
      <span class="annotation-example-solution-label">解</span>
      <span class="annotation-example-solution-text annotation-rich-text-inline" v-html="renderMarkdownInline(solution)"></span>
    </div>
  </section>
</template>
