<script setup lang="ts">
/**
 * KpiCards —— 年 / 月 / 日 / 时 四段累计产量卡
 * delta 为涨跌百分比；未提供则只显示副标题。
 */
import { hexA } from '@eep/shared';
import DeltaText from './DeltaText.vue';
import type { KpiCard } from '@/utils/types';

defineProps<{ cards: KpiCard[] }>();
</script>

<template>
  <section class="kpi-row">
    <div v-for="c in cards" :key="c.label" class="kpi">
      <div class="kpi__top">
        <span>{{ c.label }}</span>
        <span class="kpi__tag" :style="{ color: c.color, background: hexA(c.color, 0.16) }">
          {{ c.tag }}
        </span>
      </div>
      <div class="kpi__val">
        <b>{{ c.value.v }}</b><small>{{ c.value.u }}</small>
      </div>
      <div class="kpi__foot">
        <template v-if="c.delta !== undefined && c.delta !== null">
          <DeltaText :value="c.delta" :lower-is-better="c.deltaLowerIsBetter" />
        </template>
        <span v-if="c.sub" class="kpi__sub">{{ c.sub }}</span>
      </div>
    </div>
  </section>
</template>
