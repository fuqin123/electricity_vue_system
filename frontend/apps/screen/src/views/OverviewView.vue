<script setup lang="ts">
/**
 * OverviewView —— 大屏一：车间总览
 * 左列：综合单位电耗仪表 + 六线实时数据 + 车间水/汽
 * 中列：年/月/日/时 KPI + 产量与用电趋势 + 六线对比
 * 右列：今日分时 + 能源用量汇总 + 告警
 */
import { computed } from 'vue';
import { storeToRefs } from 'pinia';
import {
  big,
  int,
  plantConfig,
  PLAN_RATE,
  PROFILE,
  rise,
  wan,
  type TrendDim,
  type WorkshopData
} from '@eep/shared';
import { overviewLinesCompareOption, overviewTrendOption, todayChartOption } from '@/charts';
import { useEnergyStore } from '@/stores/energy';
import type { KpiCard } from '@/utils/types';
import TopBar from '@/components/TopBar.vue';
import PanelBox from '@/components/PanelBox.vue';
import ChartBox from '@/components/ChartBox.vue';
import GaugeRing from '@/components/GaugeRing.vue';
import DeltaText from '@/components/DeltaText.vue';
import LineList from '@/components/LineList.vue';
import UtilityPanel from '@/components/UtilityPanel.vue';
import KpiCards from '@/components/KpiCards.vue';
import EnergySummary from '@/components/EnergySummary.vue';
import AlertList from '@/components/AlertList.vue';
import SvgIcon from '@/components/SvgIcon.vue';

const store = useEnergyStore();
const { dim } = storeToRefs(store);

const ws = computed<WorkshopData | null>(() => store.workshop);

const DIMS: TrendDim[] = ['年', '月', '日', '时'];
const DIM_NOTE: Record<TrendDim, string> = {
  年: '近 5 年 · 车间合计',
  月: '近 12 个月 · 车间合计（当月为进行中）',
  日: '近 30 日 · 车间合计（今日为进行中）',
  时: '今日 24 小时 · 车间合计（虚线柱为计划）'
};

/* ── 综合单位电耗仪表 ─────────────────────────────────── */
const gauge = computed(() => ({
  actual: ws.value?.today.unitKwh ?? 0,
  target: ws.value?.targetUnitKwh ?? 8.2,
  yesterday: ws.value?.yesterday.unitKwh ?? 0
}));

/* ── KPI 卡 ───────────────────────────────────────────── */
const kpiCards = computed<KpiCard[]>(() => {
  const w = ws.value;
  if (!w) return [];
  const curH = w.currentHour;
  const h = w.hours[curH];
  const prevYear = w.yearly5.length > 1 ? w.yearly5[w.yearly5.length - 2] : null;
  const hPlan = Math.round(w.lines.reduce((a, l) => a + l.rated, 0) * PLAN_RATE * PROFILE[curH]);
  const hRef = h?.partial ? (h.projected ?? 0) : (h?.output ?? 0);
  const hDelta = hRef ? ((hRef - hPlan) / hPlan) * 100 : null;
  const rate = w.today.plan > 0 ? (w.today.output / w.today.plan) * 100 : null;
  const yq = big(w.year.output, '瓶');
  const yk = big(w.year.kwh, 'kWh');
  const mq = big(w.month.output, '瓶');
  const mk = big(w.month.kwh, 'kWh');
  const hk = big(h?.kwh ?? 0, 'kWh');

  return [
    {
      tag: '年',
      color: '#00b0f4',
      label: '本年累计产量',
      value: yq,
      delta: prevYear ? rise(w.year.output, prevYear.output) : null,
      sub: `用电 ${yk.v}${yk.u}`
    },
    {
      tag: '月',
      color: '#7c8cff',
      label: '本月累计产量',
      value: mq,
      sub: `用电 ${mk.v}${mk.u}`
    },
    {
      tag: '日',
      color: '#a78bfa',
      label: '今日累计产量',
      value: big(w.today.output, '瓶'),
      delta: rise(w.today.output, w.yesterday.output),
      sub: `完成率 ${rate === null ? '--' : rate.toFixed(1) + '%'}`
    },
    {
      tag: '时',
      color: '#f06fcb',
      label: h?.partial ? '当前小时（预计）' : '当前小时产量',
      value: { v: int(h?.partial ? (h.projected ?? 0) : (h?.output ?? 0)), u: '瓶' },
      delta: hDelta,
      sub: `用电 ${hk.v}${hk.u}`
    }
  ];
});

/* ── 图表 option ──────────────────────────────────────── */
const trendOption = computed(() => (ws.value ? overviewTrendOption(ws.value, dim.value) : null));
const compareOption = computed(() => (ws.value ? overviewLinesCompareOption(ws.value) : null));
const todayOption = computed(() =>
  ws.value
    ? todayChartOption(
        ws.value.hours,
        ws.value.lines.reduce((a, l) => a + l.rated, 0)
      )
    : null
);

/* ── 今日分时备注 ─────────────────────────────────────── */
const todayNote = computed(() => {
  const w = ws.value;
  if (!w) return '--';
  const done = w.hours.filter((x) => !x.future);
  const peak = done.reduce((a, x) => Math.max(a, x.output ?? 0), 0);
  const avg = done.length ? done.reduce((a, x) => a + (x.output ?? 0), 0) / done.length : 0;
  return `峰值 ${(peak / 1000).toFixed(1)} · 均值 ${(avg / 1000).toFixed(1)} 千瓶`;
});
</script>

<template>
  <template v-if="ws">
    <TopBar title="能源与产量数据中心">
      <template #left>
        <span class="mark"><SvgIcon name="logo" /></span>
        <div class="mark-text">
          <b>{{ plantConfig.name }}</b>
          <span>MASTER KONG BEVERAGE · {{ plantConfig.code }}</span>
        </div>
      </template>
      <template #status>
        <button class="link-admin" type="button" @click="store.refresh()">
          <span style="width: 12px; height: 12px; display: inline-flex"><SvgIcon name="refresh" /></span>
          立即刷新
        </button>
        <span class="chip chip--ok"><i />数据采集中</span>
      </template>
    </TopBar>

    <div class="rule" />

    <main
      class="board"
      style="--l1: 250px; --l2: 400px; --c1: 118px; --c2: 486px; --c3: 276px; --r1: 310px; --r2: 280px"
    >
      <!-- ══════ 左列 ══════ -->
      <div class="col">
        <PanelBox title="今日综合单位电耗" height="var(--l1)">
          <template #note>
            <span class="panel__note">车间合计 · 目标 {{ gauge.target.toFixed(2) }}</span>
          </template>
          <div class="panel__body panel__body--row gap-lg">
            <GaugeRing :actual="gauge.actual" :target="gauge.target" :yesterday="gauge.yesterday" />
            <ul class="kv">
              <li>
                <span>今日产量</span>
                <b class="t-num">
                  {{ wan(ws.today.output, 1) }}
                  <span class="c-muted" style="font-family: var(--font-cn); font-size: 11px">万瓶</span>
                </b>
              </li>
              <li>
                <span>今日用电</span>
                <b class="t-num c-cyan">
                  {{ big(ws.today.kwh, 'kWh').v }}
                  <span class="c-muted" style="font-family: var(--font-cn); font-size: 11px">{{
                    big(ws.today.kwh, 'kWh').u
                  }}</span>
                </b>
              </li>
              <li>
                <span>较昨日</span>
                <b><DeltaText :value="rise(ws.today.unitKwh, ws.yesterday.unitKwh)" lower-is-better /></b>
              </li>
            </ul>
          </div>
        </PanelBox>

        <PanelBox
          title="六条产线实时数据"
          note="本小时 · 点击查看详情"
          height="var(--l2)"
          body-class="panel__body--list"
        >
          <LineList :lines="ws.lines" :current-hour="ws.currentHour" />
        </PanelBox>

        <PanelBox title="车间水耗 / 汽耗">
          <template #note><span class="chip chip--flat">车间总表</span></template>
          <UtilityPanel :ws="ws" />
        </PanelBox>
      </div>

      <!-- ══════ 中列 ══════ -->
      <div class="col">
        <KpiCards :cards="kpiCards" style="height: var(--c1)" />

        <PanelBox
          title="产量与用电趋势"
          :hint="DIM_NOTE[dim]"
          height="var(--c2)"
          tall
          body-class="panel__body--col"
        >
          <template #note>
            <div class="tabs">
              <button
                v-for="d in DIMS"
                :key="d"
                :class="{ 'is-active': d === dim }"
                @click="store.setDim(d)"
              >
                {{ d }}
              </button>
            </div>
          </template>
          <div class="legend">
            <span class="legend__i legend__i--bar" /><span>产量（万瓶）</span>
            <span class="legend__i legend__i--line" /><span>用电量（万kWh）</span>
          </div>
          <ChartBox :option="trendOption" grow />
        </PanelBox>

        <PanelBox
          title="六条产线产量与单位电耗对比"
          note="条长＝今日产量 · 右侧为单位电耗"
          height="var(--c3)"
        >
          <ChartBox :option="compareOption" grow />
        </PanelBox>
      </div>

      <!-- ══════ 右列 ══════ -->
      <div class="col">
        <PanelBox title="今日分时产量与电耗" height="var(--r1)">
          <template #note><span class="panel__note">{{ todayNote }}</span></template>
          <ChartBox :option="todayOption" grow />
        </PanelBox>

        <PanelBox title="能源用量汇总" note="今日累计 / 昨日同期" height="var(--r2)" body-class="panel__body--list">
          <EnergySummary :ws="ws" />
        </PanelBox>

        <PanelBox title="数据与阈值告警">
          <template #note><span class="panel__note">共 {{ ws.alerts.length }} 条</span></template>
          <AlertList :alerts="ws.alerts" />
        </PanelBox>
      </div>
    </main>
  </template>
</template>
