<script setup lang="ts">
withDefaults(defineProps<{ type?: 'button' | 'submit'; variant?: 'default' | 'outline' }>(), { type: 'button', variant: 'default' })
</script>

<template><button :type="type" :class="variant === 'outline' ? 'border border-slate-300 bg-white text-slate-700' : 'bg-slate-950 text-white'" class="rounded-xl px-4 py-2 text-sm font-medium transition hover:-translate-y-px hover:shadow-sm"><slot /></button></template>
