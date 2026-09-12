<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { BookOpen, CircleAlert, Sparkles } from 'lucide-vue-next'
import LearningDocumentRenderer from './LearningDocumentRenderer.vue'
import { useAppStore } from '../../stores/app'
import { normalizeLearningDocument } from '../../lib/documentAdapter'

const app = useAppStore()
const document = computed(() => normalizeLearningDocument(app.document))
const blocked = computed(() => document.value?.status === 'blocked')

onMounted(() => app.load())
watch(() => app.error, (error) => {
  if (error) console.error('[Annotation] 学习文档加载失败:', error)
}, { immediate: true })
watch(blocked, (isBlocked) => {
  if (isBlocked) console.warn('[Annotation] 学习文档处于阻塞状态，已停止学习者呈现。')
}, { immediate: true })
</script>

<template>
  <div class="annotation-page">
    <header class="annotation-masthead">
      <div class="annotation-title-block">
        <p class="annotation-kicker">ANNOTATION / 教材档案</p>
        <div class="annotation-title-row">
          <BookOpen :size="20" stroke-width="1.5" aria-hidden="true" />
          <h1>从教材到可学习页面</h1>
        </div>
        <p class="annotation-dek">沿着章节顺序读懂概念，参考例题，完成练习，再回看教材依据。</p>
      </div>
    </header>

    <div class="annotation-rule" aria-hidden="true"></div>

    <div v-if="app.error" class="annotation-error" role="alert">
      <CircleAlert :size="16" stroke-width="1.5" aria-hidden="true" />
      <span>学习文档暂时无法打开，请稍后再试。</span>
    </div>
    <div v-else-if="app.loading" class="annotation-loading" role="status">
      <Sparkles :size="16" stroke-width="1.5" aria-hidden="true" />
      <span>正在读取教材并生成学习文档…</span>
    </div>
    <div v-else-if="blocked" class="annotation-empty" role="alert">
      此部分暂不可用，学习内容正在等待进一步核查。
    </div>
    <LearningDocumentRenderer v-else-if="document" :document="document" />
    <div v-else class="annotation-empty" role="status">当前运行没有可呈现的学习文档。</div>
  </div>
</template>

<style scoped>
.annotation-masthead {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 2rem;
}

.annotation-title-block {
  max-width: 46rem;
}

.annotation-kicker {
  margin: 0 0 0.85rem;
  color: #8b2e24;
  font: 600 0.7rem/1.2 "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
  letter-spacing: 0.18em;
}

.annotation-title-row {
  display: flex;
  align-items: center;
  gap: 0.7rem;
}

.annotation-title-row svg {
  color: #8b2e24;
  flex: 0 0 auto;
}

h1 {
  margin: 0;
  color: #171717;
  font: 600 clamp(2rem, 4vw, 3.2rem)/1.12 "Noto Serif SC", "Source Han Serif SC", "Songti SC", serif;
  letter-spacing: -0.03em;
}

.annotation-dek {
  max-width: 38rem;
  margin: 1rem 0 0;
  color: #55514b;
  font-size: 1rem;
  line-height: 1.8;
}

.annotation-rule {
  height: 1px;
  margin: 2rem 0 2.5rem;
  background: #c9c5bd;
}

.annotation-error,
.annotation-loading {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 1rem 0;
  border-top: 1px solid #c9c5bd;
  border-bottom: 1px solid #c9c5bd;
  color: #55514b;
  font-size: 0.9rem;
}

.annotation-error {
  color: #8b2e24;
}

.annotation-empty {
  padding: 1rem 0;
  border-top: 1px solid #c9c5bd;
  border-bottom: 1px solid #c9c5bd;
  color: #55514b;
  font-size: 0.9rem;
}

@media (max-width: 640px) {
  .annotation-masthead {
    display: block;
  }
}
</style>
