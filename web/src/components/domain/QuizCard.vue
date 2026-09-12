<script setup lang="ts">
import { ref } from 'vue'

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
    <h4>{{ question }}</h4>
    <div class="annotation-quiz-options">
      <button
        v-for="option in options"
        :key="option"
        type="button"
        :class="{ selected: selected === option, correct: submitted && option === answer, incorrect: submitted && selected === option && option !== answer }"
        :aria-pressed="selected === option"
        @click="choose(option)"
      >
        {{ option }}
      </button>
    </div>
    <div v-if="submitted" class="annotation-quiz-feedback" :data-correct="selected === answer">
      <strong>{{ selected === answer ? '回答正确' : '先记下这个差异' }}</strong>
      <p>答案：{{ answer }}。{{ explanation }}</p>
    </div>
  </section>
</template>
