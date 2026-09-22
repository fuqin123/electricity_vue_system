<script setup lang="ts">
/**
 * DeltaText —— 涨跌指示
 * lowerIsBetter=true 时（如单位电耗）下跌视为「好」，显示绿色。
 * 注意：这是数据大屏的语义色，非股票涨跌色，不适用红涨绿跌约定。
 */
import { computed } from 'vue';

const props = defineProps<{
  value: number | null;
  lowerIsBetter?: boolean;
}>();

const color = computed(() => {
  const p = props.value;
  if (p === null || p === undefined || Number.isNaN(p)) return 'var(--t4)';
  if (Math.abs(p) < 0.05) return 'var(--t4)';
  const up = p >= 0;
  const good = props.lowerIsBetter ? !up : up;
  return good ? 'var(--green)' : 'var(--magenta)';
});

const text = computed(() => {
  const p = props.value;
  if (p === null || p === undefined || Number.isNaN(p)) return '--';
  return `${p >= 0 ? '+' : ''}${p.toFixed(1)}%`;
});

const up = computed(() => (props.value ?? 0) >= 0);
</script>

<template>
  <span class="arrow">
    <svg viewBox="0 0 12 12" width="12" height="12">
      <path
        v-if="up"
        d="M3 9L9 3M9 3H4.2M9 3v4.8"
        :stroke="color"
        stroke-width="1.7"
        stroke-linecap="round"
        stroke-linejoin="round"
        fill="none"
      />
      <path
        v-else
        d="M3 3L9 9M9 9H4.2M9 9V4.2"
        :stroke="color"
        stroke-width="1.7"
        stroke-linecap="round"
        stroke-linejoin="round"
        fill="none"
      />
    </svg>
  </span>
  <span class="t-num" :style="{ color }">{{ text }}</span>
</template>
