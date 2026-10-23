<script setup lang="ts">
/**
 * LineView —— 大屏二：单线实时数据详情
 * 左列：产线视频监控 + 本线小时明细 + 数据上报质量
 * 中列：年/月/日/时 KPI + 可切换维度的趋势 + 7×24 热力图
 * 右列：今日分时 + 单位电耗对标 + 本线告警
 *
 * ⚠ 产量数据源尚未接入（`dataConfig.productionEnabled = false`）：
 *   产量类数值一律为 0、单位电耗为 `--`、达成率为 `--`，
 *   状态退化为按用电负荷判定。界面必须保留「产量未接入」标注，
 *   否则「产量 0 / 达成率 0%」会被误读成产线停产或严重欠产。
 */
import { computed, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useRoute, useRouter } from 'vue-router';
import {
  big,
  dataConfig,
  dec1,
  int,
  pad2,
  PLAN_RATE,
  PROFILE,
  rise,
  thresholds,
  type AlertItem,
  type HourPoint,
  type LineData,
  type TrendDim,
  type WorkshopData
} from '@eep/shared';
import { lineHeatmapOption, LINE_TREND, lineTrendOption, todayChartOption } from '@/charts';
import { useEnergyStore } from '@/stores/energy';
import type { KpiCard } from '@/utils/types';
import TopBar from '@/components/TopBar.vue';
import PanelBox from '@/components/PanelBox.vue';
import ChartBox from '@/components/ChartBox.vue';
import KpiCards from '@/components/KpiCards.vue';
import HourTable from '@/components/HourTable.vue';
import QualityBars from '@/components/QualityBars.vue';
import BenchmarkBars from '@/components/BenchmarkBars.vue';
import AlertList from '@/components/AlertList.vue';
import VideoMonitor from '@/components/VideoMonitor.vue';
import SvgIcon from '@/components/SvgIcon.vue';
import DateSwitch from '@/components/DateSwitch.vue';
import { videoConfig } from '@eep/shared';

const route = useRoute();
const router = useRouter();
const store = useEnergyStore();
/** 趋势维度与总览共用同一份状态，来回切屏不会丢 */
const { dim, selectedDate, today, isHistory, stale } = storeToRefs(store);

const lineId = computed(() => String(route.params.lineId ?? 'W103').toUpperCase());

watch(
  lineId,
  (id) => store.setLine(id),
  { immediate: true }
);

const ws = computed<WorkshopData | null>(() => store.workshop);
const line = computed<LineData | null>(() => store.currentLine);

/* ── 趋势维度（与总览同口径）───────────────────────────── */
const DIMS: TrendDim[] = ['年', '月', '日', '时'];
/**
 * 与总览同一套写法：说明里写清「每柱代表多长时段」，
 * 否则「日」和「时」两个维度在观感上分不出来（v1.2.3 反馈）。
 * X 轴一律从左向右时间递增（最右 = 本期）。
 */
const DIM_NOTE: Record<TrendDim, string> = {
  年: '每柱＝1 年 · 近 5 年 · 本线',
  月: '每柱＝1 个月 · 近 12 个月 · 本线（当月为进行中）',
  日: '每柱＝1 个生产日 · 近 30 日 · 本线（当前生产日为进行中）',
  时: '每柱＝1 小时 · 本生产日 24 小时（06:00—次日06:00） · 本线'
};

/** 单位电耗是否算得出来 —— 决定趋势第二条曲线是「单位电耗」还是「用电量」 */
const hasUnitKwh = computed(() => line.value?.hours.some((h) => h.unitKwh !== null) ?? false);

/**
 * 图例单位直接取 charts/index.ts 的 LINE_TREND —— 出数除数与单位文案同源，
 * 之前这里另写一份，改一处忘一处就会「图例说万瓶、tooltip 说亿瓶」。
 */
const trendLegend = computed(() => {
  const u = LINE_TREND[dim.value];
  return {
    out: `产量（${u.outUnit}）`,
    second: hasUnitKwh.value ? '单位电耗（kWh/千瓶）' : `用电量（${u.kwhUnit}）`
  };
});

/** 产量数据源是否已接入（来自全局配置，不用逐处判断字段是否为 0） */
const prodOn = dataConfig.productionEnabled;

/* ── 顶部信息 ─────────────────────────────────────────── */
/**
 * ⚠ 需求 v1.2.1-4：产线名称 / 额定产能 / 品项一律取**接口数据**（数据库），
 *   不再读前端静态 linesConfig —— 大屏与后台（生产管理页）必须同源一致。
 */
const profileText = computed(() => {
  const l = line.value;
  if (!l) return '--';
  return `额定产能 ${int(l.rated)} 瓶/时 · ${l.product || '--'} · 数据源：产线电表（水汽为车间总表，不可拆分至线别）`;
});

/**
 * 顶部状态
 * ⚠ 产量未接入时，状态是按**用电负荷**判出来的（后端与 mock 同口径），
 *   所以这里必须换一套文案 —— 沿用「低产运行 / 停线」会被当成产能结论。
 */
const statusChip = computed(() => {
  const s = line.value?.status ?? 'running';
  if (!prodOn) {
    const loadMap = {
      running: { cls: 'chip--ok', text: '运行中（按用电）', color: '#35ed7e' },
      slow: { cls: 'chip--warn', text: '低负荷（按用电）', color: '#ffb020' },
      stopped: { cls: 'chip--err', text: '本小时无用电', color: '#ec48bd' }
    } as const;
    return loadMap[s];
  }
  const map = {
    running: { cls: 'chip--ok', text: '运行中', color: '#35ed7e' },
    slow: { cls: 'chip--warn', text: '低产运行', color: '#ffb020' },
    stopped: { cls: 'chip--err', text: '停线', color: '#ec48bd' }
  } as const;
  return map[s];
});

/* ── KPI 卡 ───────────────────────────────────────────── */
const kpiCards = computed<KpiCard[]>(() => {
  const l = line.value;
  const w = ws.value;
  if (!l || !w) return [];
  const curH = w.currentHour;
  const h = l.hours[curH];
  const hPlan = Math.round(l.rated * PLAN_RATE * PROFILE[h?.clock ?? 0]);
  /** 当前小时产量：进行中先看整点预期，否则看实绩；产量未接入（null）→ 显示 -- */
  const hOut = h?.partial ? (h.projected ?? h.output ?? null) : (h?.output ?? null);
  const hRef = hOut ?? 0;
  const hDelta = hRef ? ((hRef - hPlan) / hPlan) * 100 : null;
  const yq = big(l.year.output, '瓶');
  const yk = big(l.year.kwh, 'kWh');
  const mq = big(l.month.output, '瓶');
  const mk = big(l.month.kwh, 'kWh');
  const dk = big(l.today.kwh, 'kWh');
  const hk = big(h?.kwh ?? 0, 'kWh');
  const target = l.standardUnitKwh;
  const overTarget = target !== null && l.today.unitKwh !== null && l.today.unitKwh > target;
  /** 产量未接入时，达成率没有意义 —— 显示 `--`，不要显示 0% */
  const rateText = prodOn ? `${l.today.rate.toFixed(1)}%` : '--';

  return [
    {
      tag: '年',
      color: '#00b0f4',
      label: '本年累计产量',
      value: yq,
      sub: `年内第 ${l.dayOfYear} 天 · 用电 ${yk.v}${yk.u}`
    },
    {
      tag: '月',
      color: '#7c8cff',
      label: '本月累计产量',
      value: mq,
      sub: `用电 ${mk.v}${mk.u} · 本生产日单耗 ${l.today.unitKwh === null ? '--' : l.today.unitKwh.toFixed(2)}${overTarget ? '（超标准）' : ''}`
    },
    {
      tag: '日',
      color: '#a78bfa',
      label: '本生产日累计产量',
      value: big(l.today.output, '瓶'),
      delta: prodOn ? rise(l.today.output, l.yesterday.output) : null,
      sub: `完成率 ${rateText} · 用电 ${dk.v}${dk.u}`
    },
    {
      tag: '时',
      color: '#f06fcb',
      // 查历史整日时第 23 小时早已结算，不能写「当前小时」（B-522）
      label: w.dayClosed
        ? `${pad2(curH)} 时段产量`
        : h?.partial
          ? '当前小时（进行中）'
          : '当前小时产量',
      value: { v: int(hOut), u: '瓶' },
      delta: prodOn ? hDelta : null,
      sub: `用电 ${hk.v}${hk.u}`
    }
  ];
});

/* ── 数据上报计数（小时明细表与质量条共用）────────────── */
const stat = computed(() => {
  const l = line.value;
  if (!l) return { received: 0, expect: 0 };
  const done = l.hours.filter((s) => !s.future);
  return {
    expect: done.length,
    received: done.filter((s) => s.kwh !== null && s.kwh !== undefined).length
  };
});

/* ── 本线告警（由当日数据实时判定，与总览口径一致）───── */
const lineAlerts = computed<AlertItem[]>(() => {
  const l = line.value;
  const w = ws.value;
  if (!l || !w) return [];
  const list: AlertItem[] = [];
  const curH = w.currentHour;

  /* 产量未接入：先给一条说明，下面的产量类判定全部跳过 */
  if (!prodOn) {
    list.push({ level: 'info', text: dataConfig.productionNote, time: '--:--:--' });
  }

  /* 单位电耗超阈值：只报当日最差的一个「已完成整点」
     ⚠ 用 for...of 而非 forEach：TS 不追踪闭包内的赋值，
       写成 forEach 时 worst 会被窄化成 null，后续取属性报 never。 */
  let worst: HourPoint | null = null;
  for (const s of l.hours) {
    if (!s.future && !s.partial && s.unitKwh !== null && s.unitKwh > thresholds.unitKwhHigh) {
      if (!worst || s.unitKwh > (worst.unitKwh ?? 0)) worst = s;
    }
  }
  if (worst) {
    const w2 = worst;
    list.push({
      level: 'err',
      text: `单位电耗 ${(w2.unitKwh ?? 0).toFixed(2)}，超阈值 ${thresholds.unitKwhHigh.toFixed(2)}`,
      time: `${pad2(w2.clock)}:00:00`
    });
  }

  /* 计划停机（CIP / 换型 / 检修）：连续时段合并为一条，并带上原因。
     这是计划窗口，不是故障，产量未接入时降为 info。 */
  const runs: [number, number][] = [];
  let start = -1;
  l.hours.forEach((s, i) => {
    if (!s.future && s.stopped) {
      if (start < 0) start = i;
    } else if (start >= 0) {
      runs.push([start, i - 1]);
      start = -1;
    }
  });
  if (start >= 0) runs.push([start, 23]);
  runs.forEach((r) => {
    const span = r[1] - r[0] + 1;
    const c0 = l.hours[r[0]]?.clock ?? 0;
    const c1 = l.hours[r[1]]?.clock ?? 0;
    list.push({
      level: prodOn ? (span >= 3 ? 'warn' : 'info') : 'info',
      text:
        (span > 1 ? `${pad2(c0)}—${pad2(c1)} 时计划停机 ${span} 小时` : `${pad2(c0)} 时计划停机`) +
        (l.note ? `（${l.note}）` : ''),
      time: `${pad2(c1)}:00:00`
    });
  });

  if (prodOn) {
    /* 电表缺失（有产量无电量） */
    l.hours.forEach((s) => {
      if (!s.future && (s.output ?? 0) > 0 && (s.kwh === null || s.kwh === 0)) {
        list.push({ level: 'err', text: '产量已上报但电表无数据', time: `${pad2(s.clock)}:00:00` });
      }
    });

    /* 本小时低产：用整点预期值判定，且至少已过 1/4 小时才判 */
    const cur = l.hours[curH];
    if (
      cur &&
      !cur.future &&
      cur.partial &&
      (cur.progress ?? 0) >= 0.25 &&
      (cur.projected ?? 0) > 0 &&
      (cur.projected ?? 0) < l.rated * thresholds.outputLowRate
    ) {
      list.unshift({
        level: 'warn',
        text: `本小时预计产量 ${((cur.projected ?? 0) / 1000).toFixed(1)} 千瓶，低于额定 ${Math.round(
          thresholds.outputLowRate * 100
        )}%`,
        time: `${pad2(cur.clock)}:00:00`
      });
    }
  }

  /* 视频：按 provider 判定是否已具备可播条件 */
  const ch = videoConfig.channels[l.id];
  if (videoConfig.enabled && videoConfig.provider === 'go2rtc') {
    if (!ch || ch.enabled === false) {
      list.push({ level: 'info', text: '本线摄像头未接入，视频暂不可预览', time: '--:--:--' });
    } else if (!videoConfig.go2rtc.base) {
      list.push({ level: 'info', text: '视频网关地址未配置，无法预览', time: '--:--:--' });
    }
  } else if (videoConfig.enabled && videoConfig.provider === 'custom') {
    const ep = videoConfig.customEndpoints[l.id];
    if (!ep || (!ep.flv && !ep.hls && !ep.webrtc)) {
      list.push({ level: 'info', text: '视频通道未配置转流网关，无法预览', time: '--:--:--' });
    }
  }

  const order: Record<AlertItem['level'], number> = { err: 0, warn: 1, info: 2 };
  return list.sort((a, b) => order[a.level] - order[b.level]);
});

/* ── 图表 option ──────────────────────────────────────── */
const trendOption = computed(() => (line.value ? lineTrendOption(line.value, dim.value) : null));
const heatmapOption = computed(() => (line.value ? lineHeatmapOption(line.value) : null));
const todayOption = computed(() =>
  line.value && ws.value
    ? todayChartOption(line.value.hours, line.value.rated, ws.value.ongoingHour ?? -1)
    : null
);

const todayNote = computed(() => {
  const l = line.value;
  if (!l) return '--';
  const done = l.hours.filter((x) => !x.future);
  /* 产量未接入时改报用电，避免显示「峰值 0.0 · 均值 0.0 千瓶」这种无信息量的数 */
  if (!prodOn) {
    const kpeak = done.reduce((a, x) => Math.max(a, x.kwh ?? 0), 0);
    const kavg = done.length ? done.reduce((a, x) => a + (x.kwh ?? 0), 0) / done.length : 0;
    return `用电峰值 ${dec1(kpeak)} · 均值 ${kavg.toFixed(1)} kWh`;
  }
  const peak = done.reduce((a, x) => Math.max(a, x.output ?? 0), 0);
  const avg = done.length ? done.reduce((a, x) => a + (x.output ?? 0), 0) / done.length : 0;
  return `峰值 ${(peak / 1000).toFixed(1)} · 均值 ${(avg / 1000).toFixed(1)} 千瓶`;
});

const tableNote = computed(() => {
  const w = ws.value;
  return w ? `最新在上 · 已完成 ${w.currentHour + 1} 点` : '--';
});

const qualityNote = computed(() => `应报 24 点 · 实收 ${stat.value.received} 点`);
const camId = computed(() => videoConfig.channels[lineId.value]?.channel ?? '--');

function switchLine(id: string) {
  void router.push({ name: 'line', params: { lineId: id } });
}
</script>

<template>
  <template v-if="ws && line">
    <TopBar title="单线实时数据详情">
      <template #left>
        <RouterLink class="btn-back" :to="{ name: 'overview' }">
          <SvgIcon name="arrow-left" />返回总览
        </RouterLink>
        <div class="mark-text">
          <b>{{ line.name }} · 实时数据</b>
          <span>PRODUCTION LINE {{ line.id }} · REAL-TIME MONITORING</span>
        </div>
      </template>
      <template #status>
        <DateSwitch :model-value="selectedDate" :today="today" :busy="stale" @update:model-value="store.setDate" />
        <span v-if="isHistory" class="chip chip--warn" title="历史数据不会再变化，已暂停自动刷新">
          <i />历史数据 · 已暂停刷新
        </span>
        <span class="chip" :class="statusChip.cls" :style="{ color: statusChip.color }">
          <i />{{ statusChip.text }}
        </span>
      </template>
    </TopBar>

    <div class="subbar">
      <div class="tabs tabs--line">
        <!-- v1.2.1-4：产线 tab 用接口数据（与后台一致），不读静态 linesConfig -->
        <button
          v-for="c in ws.lines"
          :key="c.id"
          :class="{ 'is-active': c.id === line.id }"
          @click="switchLine(c.id)"
        >
          {{ c.name }}
        </button>
      </div>
      <span v-if="!prodOn" class="chip chip--flat" :title="dataConfig.productionNote">
        <i />产量未接入
      </span>
      <span class="panel__note">{{ profileText }}</span>
    </div>

    <main class="board" style="--l1: 264px; --c1: 118px; --c2: 486px; --c3: 276px; --r1: 310px; --r2: 280px">
      <!-- ══════ 左列 ══════ -->
      <div class="col">
        <PanelBox title="产线视频监控" height="var(--l1)" body-class="panel__body--col">
          <template #note><span class="chip chip--flat">通道 {{ camId }}</span></template>
          <VideoMonitor :line-id="line.id" />
        </PanelBox>

        <PanelBox title="本线小时明细" body-class="panel__body--col">
          <template #note><span class="panel__note">{{ tableNote }}</span></template>
          <HourTable :line="line" :current-hour="ws.currentHour" :ongoing-hour="ws.ongoingHour" />
        </PanelBox>

        <PanelBox title="数据上报质量" height="158px">
          <template #note><span class="panel__note">{{ qualityNote }}</span></template>
          <QualityBars :line="line" :received="stat.received" :expect="stat.expect" />
        </PanelBox>
      </div>

      <!-- ══════ 中列 ══════ -->
      <div class="col">
        <KpiCards :cards="kpiCards" style="height: var(--c1)" />

        <PanelBox
          :title="hasUnitKwh ? '本线产量与单位电耗趋势' : '本线产量与用电趋势'"
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
            <span class="legend__i legend__i--area" /><span>{{ trendLegend.out }}</span>
            <span class="legend__i legend__i--line2" /><span>{{ trendLegend.second }}</span>
          </div>
          <ChartBox :option="trendOption" grow />
        </PanelBox>

        <PanelBox
          title="近 7 日 × 24 小时产量热力图"
          :note="prodOn ? '颜色越亮产量越高 · 空格为未上报' : '产量数据源未接入，热力图暂无数据'"
          height="var(--c3)"
          body-class="panel__body--col"
        >
          <ChartBox :option="heatmapOption" grow />
        </PanelBox>
      </div>

      <!-- ══════ 右列 ══════ -->
      <div class="col">
        <PanelBox title="本生产日分时产量与电耗" height="var(--r1)" body-class="panel__body--col">
          <template #note><span class="panel__note">{{ todayNote }}</span></template>
          <ChartBox :option="todayOption" grow />
        </PanelBox>

        <PanelBox title="单位电耗对标" note="条长＝目标/实际 · 越长越优" height="var(--r2)" body-class="panel__body--list">
          <BenchmarkBars :ws="ws" :line="line" />
        </PanelBox>

        <PanelBox title="本线告警">
          <template #note><span class="panel__note">共 {{ lineAlerts.length }} 条</span></template>
          <AlertList :alerts="lineAlerts" empty-text="本线当前无异常" />
        </PanelBox>
      </div>
    </main>
  </template>
</template>
