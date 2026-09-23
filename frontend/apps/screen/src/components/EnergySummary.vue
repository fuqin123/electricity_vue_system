<script setup lang="ts">
/**
 * EnergySummary —— 能源用量汇总（电 / 水 / 汽）
 * 展示今日累计与昨日同期对比。
 *
 * ⚠ 今日/昨日用量都可能是 null（取不到数就是未知，不是 0），
 *   一律走 big()/int() 的空值分支显示 `--`。
 */
import { big, int, rise, wan, type WorkshopData } from '@eep/shared';

const props = defineProps<{ ws: WorkshopData }>();

function rows() {
  const w = props.ws;
  /* 覆盖度：今日累计只统计「已有数据的小时」。刚上线或采集中断时，
     小时数会明显少于已过小时数 —— 必须显示出来，
     否则「今日累计」会被读成「今天几乎没用电」（对应 docs/02 的 B-403）。 */
  const cover = (c?: number, e?: number) => (c !== undefined && e !== undefined ? `已采集 ${c}/${e} 小时` : '');
  return [
    {
      key: '电',
      color: '#00b0f4',
      today: w.today.kwh,
      yest: w.yesterday.kwh,
      unit: 'kWh',
      note: '产线电表合计',
      cover: cover(w.today.kwhCoveredHours, w.today.kwhExpectedHours)
    },
    {
      key: '水',
      color: '#5865f2',
      today: w.utility.water.today,
      yest: w.utility.water.yesterday,
      unit: 't',
      note: '车间总表',
      cover: cover(w.utility.water.coveredHours, w.utility.water.expectedHours)
    },
    {
      key: '汽',
      color: '#ec48bd',
      today: w.utility.steam.today,
      yest: w.utility.steam.yesterday,
      unit: 't',
      note: '车间总表（真实采集）',
      cover: cover(w.utility.steam.coveredHours, w.utility.steam.expectedHours)
    }
  ];
}

const yestColor = (p: number | null) => (p === null ? '#6e78b4' : p > 0 ? '#ec48bd' : '#35ed7e');

/** 昨日同期文本：超过 1 万才用「万」，空值显示 `--` */
function yestText(v: number | null): string {
  if (v === null || v === undefined) return '--';
  return Math.abs(v) >= 10000 ? `${wan(v, 2)}万` : int(v);
}
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
          昨日同期 {{ yestText(r.yest) }}{{ r.unit }}
          <template v-if="rise(r.today, r.yest) !== null">
            · {{ (rise(r.today, r.yest) as number) > 0 ? '+' : '' }}{{ (rise(r.today, r.yest) as number).toFixed(1) }}%
          </template>
        </span>
        <em v-if="r.cover" class="energy-row__cover">{{ r.cover }}</em>
      </div>
    </div>
  </div>
</template>

<style scoped>
.energy-row__cover {
  display: block;
  font-size: 10px;
  font-style: normal;
  color: var(--t5);
}
.list-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
  height: 100%;
  overflow: hidden;
}
</style>
