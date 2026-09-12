<script setup lang="ts">
import type { ReviewIssue } from '../../lib/document'

defineProps<{ issue: ReviewIssue }>()
function label(issue: ReviewIssue) {
  if (issue.layer === 'fact' || issue.category === 'fact' || issue.category === 'uncertainty') return '建议核实'
  if (issue.layer === 'stance' || issue.category === 'conflict' || issue.category === 'stance') return '多角度观点'
  return '学习提示'
}
</script>

<template>
  <li class="annotation-review-issue" :data-severity="issue.severity">
    <div><span class="annotation-review-label">{{ label(issue) }}</span></div>
    <p>{{ issue.message }}</p>
  </li>
</template>
