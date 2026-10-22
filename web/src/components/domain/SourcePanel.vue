<script setup lang="ts">
import type { SourceLocations } from '../../lib/api'
defineProps<{ sourceRefs?: string[]; locations?: SourceLocations }>()
</script>

<template>
  <details v-if="sourceRefs?.length" class="annotation-source-panel">
    <summary>展开教材依据 <span>（{{ sourceRefs.length }} 条）</span></summary>
    <ol>
      <li v-for="(sourceRef, index) in sourceRefs" :key="`${sourceRef}-${index}`">
        <template v-if="locations?.[sourceRef]">教材依据 · 第 {{ locations[sourceRef].page_number }} 页 · 第 {{ locations[sourceRef].block_index + 1 }} 段</template>
        <template v-else>教材依据 · 第 {{ index + 1 }} 条（定位信息不可用）</template>
      </li>
    </ol>
  </details>
</template>
