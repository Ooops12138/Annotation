<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { InteractiveComponentSpec } from '../../lib/document'
import { renderInteractiveComponent, type InteractiveComponentRenderHandle } from '../../lib/interactiveRenderer'

const props = defineProps<{ spec: InteractiveComponentSpec }>()
const host = ref<HTMLElement | null>(null)
const unavailable = ref(false)
let handle: InteractiveComponentRenderHandle | null = null

onMounted(() => {
  if (!host.value) return
  try {
    handle = renderInteractiveComponent(host.value, props.spec)
  } catch (error) {
    unavailable.value = true
    console.error('[Annotation] 交互组件规格未通过 renderer 边界:', error)
  }
})

onBeforeUnmount(() => {
  handle?.destroy()
  handle = null
})
</script>

<template>
  <section class="interactive-component" :aria-label="spec.accessibility.aria_label">
    <div v-if="!unavailable" ref="host"></div>
    <p v-else class="interactive-component-fallback" role="alert">互动图示暂不可用。</p>
  </section>
</template>
