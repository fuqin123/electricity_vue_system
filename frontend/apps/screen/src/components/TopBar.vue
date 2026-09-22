<script setup lang="ts">
/**
 * TopBar —— 大屏顶栏（左信息 / 中标题 / 右时钟-状态）
 * 通过具名插槽装配，两块大屏共用同一骨架。
 */
import { useClock } from '@/composables/useClock';

defineProps<{ title: string; eyebrow?: string }>();

const { clock, shift } = useClock();
</script>

<template>
  <header class="topbar">
    <div class="topbar__side topbar__side--l">
      <slot name="left" />
    </div>

    <div class="topbar__center">
      <span class="eyebrow">{{ eyebrow ?? 'ENERGY & OUTPUT DATA CENTER' }}</span>
      <h1>{{ title }}</h1>
    </div>

    <div class="topbar__side topbar__side--r">
      <div class="clock">
        <b>{{ clock() }}</b>
        <span>{{ shift() }}</span>
      </div>
      <slot name="status" />
    </div>
  </header>
</template>
