<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string }>()
const markdown = new MarkdownIt({ html: false, linkify: false, typographer: false, breaks: true })
const rendered = () => DOMPurify.sanitize(markdown.render(props.content || ''), {
  ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'hr'],
  ALLOWED_ATTR: ['href', 'title', 'target', 'rel'],
  FORBID_ATTR: ['style', 'class', 'id', 'onclick', 'onerror'],
  ALLOW_DATA_ATTR: false,
})
</script>

<template>
  <!-- markdown-it is configured with html:false; model-supplied HTML is never compiled as Vue. -->
  <div class="annotation-markdown" v-html="rendered()"></div>
</template>
