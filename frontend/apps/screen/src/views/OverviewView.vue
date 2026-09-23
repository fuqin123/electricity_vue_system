<script setup lang="ts">
/**
 * OverviewView —— 大屏一：车间总览
 * 左列：综合单位电耗仪表 + 六线实时数据 + 车间水/汽
 * 中列：年/月/日/时 KPI + 产量与用电趋势（可切换维度）+ 六线对比
 * 右列：今日分时 + 能源用量汇总 + 告警
 *
 * ⚠ 产量数据源尚未接入（`dataConfig.productionEnabled = false`）：
 *   产量一律为 0、单位电耗为 `--`、达成率为 `--`。
 *   **不能**把单位电耗为 null 时兜底成 0 再画仪表 —— 0 kWh/千瓶 会被读成
 *   「能效完美」，正好与「还没接数据」相反。此处直接换成占位。
 */
import { computed } from 'vue';
import { storeToRefs } from 'pinia';
import {
  big,
  dataConfig,
  int,
  plantConfig,
  PLAN_RATE,
  PROFILE,
  rise,
  wan,
  type TrendDim,
  type WorkshopData
} from '@eep/shared';
import { overviewLinesCompareOption, overviewTrendOption, OVERVIEW_TREND, todayChartOption } from '@/charts';
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
import DateSwitch from '@/components/DateSwitch.vue';

const store = useEnergyStore();
const { dim, selectedDate, isHistory } = storeToRefs(store);

/** 产量数据源是否已接入（见 config.ts 的 dataConfig） */
const prodOn = dataConfig.productionEnabled;

const ws = computed<WorkshopData | null>(() => store.workshop);

const DIMS: TrendDim[] = ['年', '月', '日', '时'];
/**
 * 说明里必须写清「每柱代表多长时段」：维度 tab 只有「年/月/日/时」一个字，
 * 看的人分不清这张图画的是每天一根还是每小时一根（v1.2.3 反馈）。
 * X 轴一律**从左向右时间递增**（最右 = 本期），与后端 daily30/monthly12/yearly5 同序。
 */
const DIM_NOTE: Record<TrendDim, string> = {
  年: '每柱＝1 年 · 近 5 年 · 车间合计',
  月: '每柱＝1 个月 · 近 12 个月 · 车间合计（当月为进行中）',
  日: '每柱＝1 天 · 近 30 天 · 车间合计（今日为进行中）',
  时: '每柱＝1 小时 · 今日 24 小时 · 车间合计（虚线柱为计划）'
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
  /** yearly5 按时间正序，末项是今年、倒数第二项是去年（同比只用这一种取法） */
  const prevYear = w.yearly5.length > 1 ? w.yearly5[w.yearly5.length - 2] : null;
  const hPlan = Math.round(w.lines.reduce((a, l) => a + l.rated, 0) * PLAN_RATE * PROFILE[curH]);
  /** 当前小时产量：进行中先看整点预期，否则看实绩；产量未接入（null）→ 显示 -- */
  const hOut = h?.partial ? (h.projected ?? h.output ?? null) : (h?.output ?? null);
  const hRef = hOut ?? 0;
  const hDelta = hRef ? ((hRef - hPlan) / hPlan) * 100 : null;
  const rate = w.today.plan > 0 && w.today.output !== null ? (w.today.output / w.today.plan) * 100 : null;
  const yq = big(w.year.output, '瓶');
  const yk = big(w.year.kwh, 'kWh');
  const mq = big(w.month.output, '瓶');
  const mk = big(w.month.kwh, 'kWh');
  const hk = big(h?.kwh ?? 0, 'kWh');
  /** 产量未接入时，达成率无意义 —— 显示 `--`，不要显示 0% */
  const rateText = prodOn && rate !== null ? `${rate.toFixed(1)}%` : '--';

  return [
    {
      tag: '年',
      color: '#00b0f4',
      label: '本年累计产量',
      value: yq,
      delta: prodOn && prevYear ? rise(w.year.output, prevYear.output) : null,
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
      delta: prodOn ? rise(w.today.output, w.yesterday.output) : null,
      sub: `完成率 ${rateText} · 用电 ${big(w.today.kwh, 'kWh').v}${big(w.today.kwh, 'kWh').u}`
    },
    {
      tag: '时',
      color: '#f06fcb',
      label: h?.partial ? '当前小时（进行中）' : '当前小时产量',
      value: { v: int(hOut), u: '瓶' },
      delta: prodOn ? hDelta : null,
      sub: `用电 ${hk.v}${hk.u}`
    }
  ];
});

/* ── 图表 option ──────────────────────────────────────── */
/** 图例单位与出数除数同源（见 charts/index.ts 的 OVERVIEW_TREND） */
const trendUnits = computed(() => OVERVIEW_TREND[dim.value]);
/** 六线对比面板的说明必须跟着图表的自适应口径走，否则文字会指着一张画了别的东西的图 */
const compareNote = computed(() =>
  prodOn
    ? '条长＝今日产量 · 右侧为单位电耗'
    : '条长＝今日用电量 · 右侧为占车间合计（产量未接入，算不出单位电耗）'
);
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
  /* 产量未接入时改报用电，避免显示「峰值 0.0 · 均值 0.0 千瓶」这种无信息量的数 */
  if (!prodOn) {
    const kpeak = done.reduce((a, x) => Math.max(a, x.kwh ?? 0), 0);
    const kavg = done.length ? done.reduce((a, x) => a + (x.kwh ?? 0), 0) / done.length : 0;
    return `用电峰值 ${kpeak.toFixed(0)} · 均值 ${kavg.toFixed(1)} kWh`;
  }
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
        <DateSwitch :model-value="selectedDate" @update:model-value="store.setDate" />
        <button class="link-admin" type="button" @click="store.refresh()">
          <span style="width: 12px; height: 12px; display: inline-flex"><SvgIcon name="refresh" /></span>
          立即刷新
        </button>
        <span v-if="!prodOn" class="chip chip--warn" :title="dataConfig.productionNote">
          <i />产量未接入
        </span>
        <!-- 历史模式下不能再写「采集中」，否则看的人会以为这是实时读数 -->
        <span v-if="isHistory" class="chip chip--warn" title="历史数据不会再变化，已暂停自动刷新">
          <i />历史数据 · 已暂停刷新
        </span>
        <span v-else class="chip chip--ok"><i />用电数据采集中</span>
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
            <span class="panel__note">
              车间合计 · {{ prodOn ? `目标 ${gauge.target.toFixed(2)}` : '产量未接入，暂不计算' }}
            </span>
          </template>
          <div class="panel__body panel__body--row gap-lg">
            <GaugeRing
              v-if="prodOn"
              :actual="gauge.actual"
              :target="gauge.target"
              :yesterday="gauge.yesterday"
            />
            <!-- 产量未接入：单位电耗 = 电量 / 产量，分母为 0 算不出来。
                 这里必须给占位，**不能**兜底成 0 —— 0 kWh/千瓶 会被读成「能效完美」。 -->
            <div v-else class="nodata-ring">
              <b>--</b>
              <span>产量未接入</span>
              <em>单位电耗 = 用电量 ÷ 产量<br />产量数据源待改造</em>
            </div>
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
                <b>
                  <DeltaText v-if="prodOn" :value="rise(ws.today.unitKwh, ws.yesterday.unitKwh)" lower-is-better />
                  <span v-else class="c-muted t-num">--</span>
                </b>
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
            <span class="legend__i legend__i--bar" /><span>产量（{{ trendUnits.outUnit }}）</span>
            <span class="legend__i legend__i--line" /><span>用电量（{{ trendUnits.kwhUnit }}）</span>
          </div>
          <ChartBox :option="trendOption" grow />
        </PanelBox>

        <PanelBox
          :title="prodOn ? '六条产线产量与单位电耗对比' : '六条产线用电量对比'"
          :note="compareNote"
          height="var(--c3)"
          body-class="panel__body--col"
        >
          <ChartBox :option="compareOption" grow />
        </PanelBox>
      </div>

      <!-- ══════ 右列 ══════ -->
      <div class="col">
        <PanelBox title="今日分时产量与电耗" height="var(--r1)" body-class="panel__body--col">
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
