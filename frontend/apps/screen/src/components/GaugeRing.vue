<script setup lang="ts">
/**
 * GaugeRing —— 今日综合单位电耗环形仪表
 * 环长 = 目标 / 实际（达成度），±1% 容差内保持品牌渐变，
 * 超出转琥珀，明显超标（>108%）转品红。
 */
import { computed } from 'vue';

const props = defineProps<{
  actual: number;
  target: number;
  yesterday: number;
}>();

const R = 58;
const CIRC = 2 * Math.PI * R;

const score = computed(() => (props.actual > 0 ? Math.min(props.target / props.actual, 1) : 0));

const dashArray = computed(() => {
  const s = CIRC * score.value;
  return `${s.toFixed(1)} ${(CIRC - s).toFixed(1)}`;
});

const strokeColor = computed(() => {
  const band = props.target * 0.01;
  if (props.actual > props.target * 1.08) return '#ec48bd';
  if (props.actual > props.target + band) return '#ffb020';
  return ''; // 空字符串表示使用 SVG 渐变
});
</script>

<template>
  <div class="gauge">
    <svg viewBox="0 0 150 150" class="gauge__svg">
      <defs>
        <linearGradient id="gaugeGrad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#00b0f4" />
          <stop offset="60%" stop-color="#5865f2" />
          <stop offset="100%" stop-color="#ec48bd" />
        </linearGradient>
      </defs>
      <circle cx="75" cy="75" :r="R" class="gauge__track" />
      <circle
        cx="75"
        cy="75"
        :r="R"
        class="gauge__arc"
        :stroke-dasharray="dashArray"
        :style="strokeColor ? { stroke: strokeColor } : undefined"
        transform="rotate(-90 75 75)"
      />
      <circle cx="75" cy="75" r="42" class="gauge__inner" />
    </svg>
    <div class="gauge__center">
      <b>{{ actual > 0 ? actual.toFixed(2) : '--' }}</b>
      <span>kWh/千瓶</span>
    </div>
  </div>
</template>
