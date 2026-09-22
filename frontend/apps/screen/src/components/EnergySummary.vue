<script setup lang="ts">
/**
 * EnergySummary —— 能源用量汇总（电 / 水 / 汽）
 * 展示今日累计与昨日同期对比。
 */
import { big, int, rise, wan, type WorkshopData } from '@eep/shared';

const props = defineProps<{ ws: WorkshopData }>();

function rows() {
  const w = props.ws;
  return [
    { key: '电', color: '#00b0f4', today: w.today.kwh, yest: w.yesterday.kwh, unit: 'kWh', note: '产线电表合计' },
    { key: '水', color: '#5865f2', today: w.utility.water.today, yest: w.utility.water.yesterday, unit: 't', note: '车间总表' },
    { key: '汽', color: '#ec48bd', today: w.utility.steam.today, yest: w.utility.steam.yesterday, unit: 't', note: '车间总表' }
  ];
}

const yestColor = (p: number | null) =>
  p === null ? '#6e78b4' : p > 0 ? '#ec48bd' : '#35ed7e';
</script>

<template>
  <div class="list-wrap">
    <div v-for="r in rows()" :key="r.key" class="energy-row">
      <div class="energy-row__label">
        <i :style="{ background: r.color }" />
        <div>
          <b>{{ r.key }}</b>
          <span>{{ r.note }}</span>
        </div>
      </div>
      <div class="energy-row__val">
        <b>
          {{ big(r.today, '').v }}<span style="font-size:10px;color:var(--t4);margin-left:3px">{{
            big(r.today, '').u + r.unit
          }}</span>
        </b>
        <span :style="{ color: yestColor(rise(r.today, r.yest)) }">
          昨日同期 {{ r.yest >= 10000 ? wan(r.yest, 2) + '万' : int(r.yest) }}{{ r.unit }}
          <template v-if="rise(r.today, r.yest) !== null">
            · {{ (rise(r.today, r.yest) as number) > 0 ? '+' : '' }}{{ (rise(r.today, r.yest) as number).toFixed(1) }}%
          </template>
        </span>
      </div>
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
