<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowUpRight, BookOpen } from 'lucide-vue-next'
import type { LearningDocument, ReviewIssue } from '../../lib/document'
import DocumentNodeRenderer from './DocumentNodeRenderer.vue'
import ReviewIssueBadge from './ReviewIssueBadge.vue'

const props = defineProps<{ document: LearningDocument }>()
const learnerNotes = computed<ReviewIssue[]>(() => [
  ...(props.document.issues ?? []),
].filter((issue) => issue.severity !== 'blocking' && (issue.layer === 'fact' || issue.layer === 'stance' || issue.category === 'fact' || issue.category === 'uncertainty' || issue.category === 'conflict' || issue.category === 'stance')))
const expandedSections = ref<Record<string, boolean>>({})

function toggleSection(id: string) {
  expandedSections.value[id] = !expandedSections.value[id]
}
</script>

<template>
  <article class="learning-document" aria-label="在线学习文档">
    <header class="document-header">
      <div>
        <p class="document-eyebrow"><BookOpen :size="14" stroke-width="1.5" aria-hidden="true" /> 教材章节</p>
        <h2>{{ document.title }}</h2>
      </div>
    </header>

    <div class="document-rule" aria-hidden="true"></div>

    <div class="document-layout">
      <aside class="document-outline" aria-label="章节导航">
        <div class="outline-heading"><span>学习路径</span><ArrowUpRight :size="14" stroke-width="1.5" aria-hidden="true" /></div>
        <a v-for="(section, index) in document.sections" :key="section.id" :href="`#${section.id}`" class="outline-link">
          <span>{{ String(index + 1).padStart(2, '0') }}</span>{{ section.title }}
        </a>
      </aside>

      <main class="document-main">
        <section v-for="(section, sectionIndex) in document.sections" :id="section.id" :key="section.id" class="document-section">
          <div class="section-heading">
            <p>章节 {{ String(sectionIndex + 1).padStart(2, '0') }}</p>
            <button type="button" class="section-toggle" :aria-expanded="expandedSections[section.id] !== false" @click="toggleSection(section.id)">
              {{ expandedSections[section.id] === false ? '展开' : '收起' }}
            </button>
          </div>
          <h3>{{ section.title }}</h3>
          <div v-if="expandedSections[section.id] !== false" class="section-nodes">
            <DocumentNodeRenderer v-for="(node, nodeIndex) in section.children" :key="`${section.id}-${nodeIndex}`" :node="node" :index="nodeIndex" />
          </div>
        </section>
      </main>

      <aside class="document-aside" aria-label="学习提示与教材依据">
        <section v-if="learnerNotes.length" class="aside-section learning-notes-section">
          <p class="aside-label">学习时多想一步</p>
          <p class="aside-note">这些提示帮助你区分教材事实、尚待核查的内容和不同观点。</p>
          <ul class="review-list"><ReviewIssueBadge v-for="issue in learnerNotes" :key="issue.issue_id" :issue="issue" /></ul>
        </section>
      </aside>
    </div>
  </article>
</template>

<style scoped>
.learning-document { color: #171717; }
.document-header { display: flex; align-items: flex-start; gap: 2rem; }
.document-eyebrow, .outline-heading, .section-heading p, .aside-label, .annotation-node-label { font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace; }
.document-eyebrow { display: flex; align-items: center; gap: .45rem; margin: 0; color: #8b2e24; font-size: .7rem; letter-spacing: .12em; text-transform: uppercase; }
.document-header h2 { max-width: 50rem; margin: .9rem 0 0; font: 600 clamp(2rem, 4vw, 3.45rem)/1.12 "Noto Serif SC", "Source Han Serif SC", "Songti SC", serif; letter-spacing: -.035em; }
.document-rule { height: 1px; margin: 2rem 0 3rem; background: #c9c5bd; }
.document-layout { display: grid; grid-template-columns: minmax(9rem, 13rem) minmax(0, 1fr) minmax(12rem, 15rem); gap: clamp(2rem, 5vw, 5rem); align-items: start; }
.document-outline, .document-aside { position: sticky; top: 5rem; }
.outline-heading { display: flex; justify-content: space-between; align-items: center; padding-bottom: .8rem; border-bottom: 1px solid #c9c5bd; color: #55514b; font-size: .7rem; letter-spacing: .1em; text-transform: uppercase; }
.outline-heading svg { color: #8b2e24; }
.outline-link { display: flex; gap: .65rem; padding: .85rem 0; border-bottom: 1px solid #dedad2; color: #55514b; font-size: .82rem; line-height: 1.5; text-decoration: none; transition: color .15s ease; }
.outline-link span { color: #8b2e24; font: .68rem/1.5 "IBM Plex Mono", monospace; }
.outline-link:hover { color: #8b2e24; }
.document-section { scroll-margin-top: 5rem; padding-bottom: 4rem; }
.document-section + .document-section { padding-top: 1rem; border-top: 1px solid #c9c5bd; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.section-heading p { margin: 0; color: #8b2e24; font-size: .68rem; letter-spacing: .12em; text-transform: uppercase; }
.section-toggle { border: 0; padding: 0; background: transparent; color: #88837b; font: .68rem/1.2 "IBM Plex Mono", monospace; cursor: pointer; }
.section-toggle:hover { color: #8b2e24; }
.document-section h3 { margin: .85rem 0 1.75rem; font: 600 clamp(1.6rem, 3vw, 2.35rem)/1.25 "Noto Serif SC", "Source Han Serif SC", "Songti SC", serif; }
.section-nodes { display: grid; gap: 1.6rem; }
.document-aside { display: grid; gap: 2rem; }
.aside-section { padding-top: .85rem; border-top: 1px solid #c9c5bd; }
.aside-label { margin: 0; color: #8b2e24; font-size: .68rem; letter-spacing: .12em; text-transform: uppercase; }
.aside-note { margin: .85rem 0 0; color: #88837b; font-size: .76rem; line-height: 1.65; }
.review-list { display: grid; gap: .8rem; margin: 1rem 0 0; padding: 0; list-style: none; }
@media (max-width: 1050px) { .document-layout { grid-template-columns: minmax(8rem, 12rem) minmax(0, 1fr); } .document-aside { position: static; grid-column: 1 / -1; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1.5rem 2rem; } }
@media (max-width: 700px) { .document-header { display: block; } .document-layout { display: block; } .document-outline, .document-aside { position: static; } .document-outline { margin-bottom: 2.5rem; } .document-aside { display: grid; grid-template-columns: 1fr; margin-top: 1rem; } }
</style>
