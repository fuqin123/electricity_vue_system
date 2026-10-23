<script setup lang="ts">
/**
 * UtilityPanel —— 车间水耗 / 汽耗双指标块（仅车间总表，不可拆分至线别）
 */
import { computed } from 'vue';
import ChartBox from './ChartBox.vue';
import { utilitySparkOption } from '@/charts';
import { dec1, hexA, int, rise, type WorkshopData } from '@eep/shared';

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
        <span>{{ it.name }}耗 · {{ props.ws.dayClosed ? '当日累计' : '本生产日累计' }}</span>
        <b>{{ int(it.d.today) }}<small>t</small></b>
        <em :style="{ color: (rise(it.d.today, it.d.yesterday) ?? 0) > 0 ? '#ec48bd' : '#35ed7e' }">
          <template v-if="it.d.rateNow !== null">瞬时 {{ dec1(it.d.rateNow) }} t/h · </template>较昨日
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
