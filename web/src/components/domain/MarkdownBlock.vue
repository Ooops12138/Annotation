<script setup lang="ts">
import { renderMarkdown } from '../../lib/markdown'

const props = defineProps<{ content: string }>()
const rendered = () => renderMarkdown(props.content)
</script>

<template>
  <!-- markdown-it is configured with html:false; model-supplied HTML is never compiled as Vue. -->
  <div class="annotation-markdown" v-html="rendered()"></div>
</template>
