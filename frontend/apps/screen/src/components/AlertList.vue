<script setup lang="ts">
/**
 * AlertList —— 告警列表（严重 / 警告 / 提示）
 * 默认最多展示 4 条，避免刷屏；无告警时给出正向提示。
 */
import { hexA, type AlertItem } from '@eep/shared';

const props = withDefaults(
  defineProps<{
    alerts: AlertItem[];
    limit?: number;
    emptyText?: string;
  }>(),
  { limit: 4, emptyText: '当前无告警 · 各项指标正常' }
);

const LEVEL: Record<AlertItem['level'], { t: string; c: string }> = {
  err: { t: '严重', c: '#ec48bd' },
  warn: { t: '警告', c: '#ffb020' },
  info: { t: '提示', c: '#00b0f4' }
};

const shown = () => props.alerts.slice(0, props.limit);
</script>

<template>
  <div class="list-wrap">
    <div v-if="!shown().length" class="empty-hint">{{ emptyText }}</div>
    <div
      v-for="(a, i) in shown()"
      :key="`${a.time}-${i}`"
      class="alert"
      :style="{ background: hexA(LEVEL[a.level].c, 0.1) }"
    >
      <span class="alert__bar" :style="{ background: LEVEL[a.level].c }" />
      <div class="alert__text">
        <b>{{ a.text }}</b>
        <span>{{ a.time }}</span>
      </div>
      <span class="alert__lvl" :style="{ color: LEVEL[a.level].c }">{{ LEVEL[a.level].t }}</span>
    </div>
  </div>
</template>

<style scoped>
.list-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
  height: 100%;
  overflow: hidden;
}
</style>
