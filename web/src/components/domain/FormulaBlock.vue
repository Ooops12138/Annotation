<script setup lang="ts">
import katex from 'katex'
import 'katex/dist/katex.min.css'

const props = defineProps<{ latex: string }>()
function renderFormula() {
  try {
    return katex.renderToString(props.latex || '', { displayMode: true, throwOnError: false, trust: false })
  } catch {
    return '<span class="annotation-formula-error">公式暂时无法渲染，请回看教材来源。</span>'
  }
}
</script>

<template>
  <figure class="annotation-formula" aria-label="公式">
    <div class="annotation-formula-math" v-html="renderFormula()"></div>
    <figcaption>公式节点 · 由受控 KaTeX 渲染</figcaption>
  </figure>
</template>
