<script setup lang="ts">
import { onMounted } from 'vue'
import { CircleAlert } from 'lucide-vue-next'
import type { RejectedNodeRecord } from '../../lib/documentRegistry'
import { asDocumentNode, rejectedNodeRecord, resolveNodeRenderer } from '../../lib/documentRegistry'
import MarkdownBlock from './MarkdownBlock.vue'
import CalloutBlock from './CalloutBlock.vue'
import QuizCard from './QuizCard.vue'
import InteractiveComponentBlock from './InteractiveComponentBlock.vue'
import type { SourceLocations } from '../../lib/api'
import SourcePanel from './SourcePanel.vue'

const props = defineProps<{ node: any; index?: number; unavailableTargets?: string[]; sourceLocations?: SourceLocations }>()
const emit = defineEmits<{ rejected: [record: RejectedNodeRecord] }>()
const renderer = () => resolveNodeRenderer(props.node)
onMounted(() => {
  if (!renderer()) {
    const record = rejectedNodeRecord(props.node, props.index)
    console.error('[Annotation] 拒绝渲染不受支持的学习内容节点:', record)
    emit('rejected', record)
  }
})
</script>

<template>
  <div v-if="asDocumentNode(node) && !unavailableTargets?.includes(node.id)" class="annotation-node">
    <MarkdownBlock v-if="renderer() === 'markdown'" :content="node.content" />
    <CalloutBlock v-else-if="renderer() === 'callout'" :tone="node.tone" :title="node.title" :content="node.content" />
    <QuizCard v-else-if="renderer() === 'quiz'" :question="node.question" :options="node.options" :answer="node.answer" :explanation="node.explanation" />
    <InteractiveComponentBlock v-else-if="renderer() === 'interactive_component'" :spec="node.spec" />
    <SourcePanel :source-refs="node.source_refs" :locations="sourceLocations" />
  </div>
  <div v-else class="annotation-rejected-node" role="alert">
    <CircleAlert :size="16" stroke-width="1.5" aria-hidden="true" />
    <span>此部分暂不可用。</span>
  </div>
</template>
