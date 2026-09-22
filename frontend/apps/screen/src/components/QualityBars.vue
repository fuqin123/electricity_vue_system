<script setup lang="ts">
/**
 * QualityBars —— 数据上报质量
 * 电量 / 产量上报率 + 缺失时段与计划停机时段清单。
 */
import { computed } from 'vue';
import { pad2, type LineData } from '@eep/shared';

const props = defineProps<{
  line: LineData;
  received: number;
  expect: number;
}>();

const rate = computed(() => (props.expect ? Math.round((props.received / props.expect) * 1000) / 10 : 0));

const missHours = computed(() =>
  props.line.hours
    .filter((s) => !s.future && (s.kwh === null || s.kwh === undefined))
    .map((s) => pad2(s.hour))
);

const stoppedHours = computed(() =>
  props.line.hours.filter((s) => !s.future && s.stopped).map((s) => pad2(s.hour))
);
</script>

<template>
  <div class="quality">
    <div class="quality__row">
      <span>电量上报</span>
      <div class="quality__bar"><i :style="{ width: rate + '%' }" /></div>
      <b>{{ rate.toFixed(1) }}%</b>
    </div>
    <div class="quality__row">
      <span>产量上报</span>
      <div class="quality__bar"><i :style="{ width: rate + '%' }" /></div>
      <b>{{ rate.toFixed(1) }}%</b>
    </div>
    <div class="quality__miss">
      {{ missHours.length ? `缺失时段：${missHours.join('、')} 时` : '今日各整点数据完整' }}
      <template v-if="stoppedHours.length"> · 计划停机：{{ stoppedHours.join('、') }} 时</template>
    </div>
  </div>
</template>
