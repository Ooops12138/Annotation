<script setup lang="ts">
defineProps<{ title: string; problem: string; solution: string }>()
</script>

<template>
  <section class="annotation-example" aria-label="例题">
    <p class="annotation-node-label">例题</p>
    <h4>{{ title }}</h4>
    <p class="annotation-example-problem">{{ problem }}</p>
    <div class="annotation-example-solution"><span>解</span>{{ solution }}</div>
  </section>
</template>
