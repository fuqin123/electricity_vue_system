<script setup lang="ts">
/**
 * UtilityPanel —— 车间水耗 / 汽耗双指标块（仅车间总表，不可拆分至线别）
 */
import { computed } from 'vue';
import ChartBox from './ChartBox.vue';
import { utilitySparkOption } from '@/charts';
import { hexA, int, rise, type WorkshopData } from '@eep/shared';

const props = defineProps<{ ws: WorkshopData }>();

const items = computed(() => [
  { name: '水', d: props.ws.utility.water, color: '#00b0f4' },
  { name: '汽', d: props.ws.utility.steam, color: '#ec48bd' }
]);

const sparkOption = computed(() => utilitySparkOption(props.ws.utility));
</script>

<template>
  <div class="panel__body panel__body--col util-body">
    <div class="dual-metric">
      <div
        v-for="it in items"
        :key="it.name"
        class="dual-metric__item"
        :style="{ background: hexA(it.color, 0.09) }"
      >
        <span>{{ it.name }}耗 · 今日累计</span>
        <b>{{ int(it.d.today) }}<small>t</small></b>
        <em :style="{ color: (rise(it.d.today, it.d.yesterday) ?? 0) > 0 ? '#ec48bd' : '#35ed7e' }">
          瞬时 {{ it.d.rateNow.toFixed(1) }} t/h · 较昨日
          {{
            rise(it.d.today, it.d.yesterday) === null
              ? '--'
              : ((rise(it.d.today, it.d.yesterday) as number) > 0 ? '+' : '') +
                (rise(it.d.today, it.d.yesterday) as number).toFixed(1) +
                '%'
          }}
        </em>
      </div>
    </div>
    <ChartBox class="spark" :option="sparkOption" grow />
  </div>
</template>

<style scoped>
.util-body {
  height: 100%;
}
</style>
