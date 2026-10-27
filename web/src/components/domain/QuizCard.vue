<script setup lang="ts">
import { ref } from 'vue'
import { renderMarkdown } from '../../lib/markdown'

const props = defineProps<{ question: string; options: string[]; answer: string; explanation: string }>()
const selected = ref<string | null>(null)
const submitted = ref(false)
function choose(option: string) {
  selected.value = option
  submitted.value = true
}
</script>

<template>
  <section class="annotation-quiz" aria-label="练习">
    <p class="annotation-node-label">练习 · 先想一步</p>
    <div class="annotation-markdown annotation-quiz-question" v-html="renderMarkdown(question)"></div>
    <div class="annotation-quiz-options">
      <button
        v-for="option in options"
        :key="option"
        type="button"
        :class="{ selected: selected === option, correct: submitted && option === answer, incorrect: submitted && selected === option && option !== answer }"
        :aria-pressed="selected === option"
        @click="choose(option)"
      >
        <div class="annotation-markdown annotation-quiz-content" v-html="renderMarkdown(option)"></div>
      </button>
    </div>
    <div v-if="submitted" class="annotation-quiz-feedback" :data-correct="selected === answer">
      <strong>{{ selected === answer ? '回答正确' : '先记下这个差异' }}</strong>
      <div class="annotation-quiz-answer">
        <span>答案：</span>
        <div class="annotation-markdown annotation-quiz-content" v-html="renderMarkdown(answer)"></div>
      </div>
      <div class="annotation-markdown annotation-quiz-content" v-html="renderMarkdown(explanation)"></div>
    </div>
  </section>
</template>

<style scoped>
.annotation-quiz-question { margin-bottom: .75rem; }
.annotation-quiz-content { min-width: 0; text-align: left; }
.annotation-quiz :deep(.annotation-markdown) { font: inherit; }
.annotation-quiz :deep(.annotation-markdown > :last-child) { margin-bottom: 0; }
.annotation-quiz :deep(pre),
.annotation-quiz :deep(.katex-display) { max-width: 100%; overflow-x: auto; }
</style>
