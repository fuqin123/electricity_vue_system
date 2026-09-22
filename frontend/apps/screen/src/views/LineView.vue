<script setup lang="ts">
/**
 * LineView —— 大屏二：单线实时数据详情
 * 左列：产线视频监控 + 本线小时明细 + 数据上报质量
 * 中列：年/月/日/时 KPI + 30 日趋势 + 7×24 热力图
 * 右列：今日分时 + 单位电耗对标 + 本线告警
 */
import { computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  big,
  int,
  linesConfig,
  pad2,
  PLAN_RATE,
  PROFILE,
  rise,
  thresholds,
  type AlertItem,
  type HourPoint,
  type LineData,
  type WorkshopData
} from '@eep/shared';
import { lineHeatmapOption, lineTrendOption, todayChartOption } from '@/charts';
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
import { videoConfig } from '@eep/shared';

const route = useRoute();
const router = useRouter();
const store = useEnergyStore();

const lineId = computed(() => String(route.params.lineId ?? 'A1').toUpperCase());

watch(
  lineId,
  (id) => store.setLine(id),
  { immediate: true }
);

const ws = computed<WorkshopData | null>(() => store.workshop);
const line = computed<LineData | null>(() => store.currentLine);

/* ── 顶部信息 ─────────────────────────────────────────── */
const profileText = computed(() => {
  const l = line.value;
  if (!l) return '--';
  const cfg = linesConfig.find((c) => c.id === l.id);
  return `额定产能 ${int(l.rated)} 瓶/时 · ${cfg?.product ?? ''} · 数据源：产线电表 + 产量计数器（水汽为车间总表，不可拆分至线别）`;
});

const statusChip = computed(() => {
  const s = line.value?.status ?? 'running';
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
  const hPlan = Math.round(l.rated * PLAN_RATE * PROFILE[curH]);
  const hRef = h?.partial ? (h.projected ?? 0) : (h?.output ?? 0);
  const hDelta = hRef ? ((hRef - hPlan) / hPlan) * 100 : null;
  const yq = big(l.year.output, '瓶');
  const yk = big(l.year.kwh, 'kWh');
  const mq = big(l.month.output, '瓶');
  const mk = big(l.month.kwh, 'kWh');
  const dk = big(l.today.kwh, 'kWh');
  const hk = big(h?.kwh ?? 0, 'kWh');
  const overTarget = l.today.unitKwh !== null && l.today.unitKwh > 8.2;

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
      sub: `用电 ${mk.v}${mk.u} · 今日单耗 ${l.today.unitKwh === null ? '--' : l.today.unitKwh.toFixed(2)}${overTarget ? '（超目标）' : ''}`
    },
    {
      tag: '日',
      color: '#a78bfa',
      label: '今日累计产量',
      value: big(l.today.output, '瓶'),
      delta: rise(l.today.output, l.yesterday.output),
      sub: `完成率 ${l.today.rate.toFixed(1)}% · 用电 ${dk.v}${dk.u}`
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
    const w = worst;
    list.push({
      level: 'err',
      text: `单位电耗 ${(w.unitKwh ?? 0).toFixed(2)}，超阈值 ${thresholds.unitKwhHigh.toFixed(2)}`,
      time: `${pad2(w.hour)}:00:00`
    });
  }

  /* 停线：连续时段合并为一条，并带上原因 */
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
    list.push({
      level: span >= 3 ? 'warn' : 'info',
      text:
        (span > 1 ? `${pad2(r[0])}—${pad2(r[1])} 时连续 ${span} 小时停机` : `${pad2(r[0])} 时停机`) +
        (l.note ? `（${l.note}）` : ''),
      time: `${pad2(r[1])}:00:00`
    });
  });

  /* 电表缺失 */
  l.hours.forEach((s) => {
    if (!s.future && (s.output ?? 0) > 0 && (s.kwh === null || s.kwh === 0)) {
      list.push({ level: 'err', text: '产量已上报但电表无数据', time: `${pad2(s.hour)}:00:00` });
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
      time: `${pad2(curH)}:00:00`
    });
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
const trendOption = computed(() => (line.value ? lineTrendOption(line.value) : null));
const heatmapOption = computed(() => (line.value ? lineHeatmapOption(line.value) : null));
const todayOption = computed(() => (line.value ? todayChartOption(line.value.hours, line.value.rated) : null));

const todayNote = computed(() => {
  const l = line.value;
  if (!l) return '--';
  const done = l.hours.filter((x) => !x.future);
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
        <span class="chip" :class="statusChip.cls" :style="{ color: statusChip.color }">
          <i />{{ statusChip.text }}
        </span>
      </template>
    </TopBar>

    <div class="subbar">
      <div class="tabs tabs--line">
        <button
          v-for="c in linesConfig"
          :key="c.id"
          :class="{ 'is-active': c.id === line.id }"
          @click="switchLine(c.id)"
        >
          {{ c.name }}
        </button>
      </div>
      <span class="panel__note">{{ profileText }}</span>
    </div>

    <main class="board" style="--l1: 264px; --c1: 118px; --c2: 486px; --c3: 276px; --r1: 310px; --r2: 280px">
      <!-- ══════ 左列 ══════ -->
      <div class="col">
        <PanelBox title="产线视频监控" height="var(--l1)">
          <template #note><span class="chip chip--flat">通道 {{ camId }}</span></template>
          <VideoMonitor :line-id="line.id" />
        </PanelBox>

        <PanelBox title="本线小时明细" body-class="panel__body--col">
          <template #note><span class="panel__note">{{ tableNote }}</span></template>
          <HourTable :line="line" :current-hour="ws.currentHour" />
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
          title="本线产量与单位电耗趋势"
          hint="近 30 日"
          height="var(--c2)"
          tall
          body-class="panel__body--col"
        >
          <template #note>
            <div class="legend legend--inline">
              <span class="legend__i legend__i--area" /><span>日产量（万瓶）</span>
              <span class="legend__i legend__i--line2" /><span>单位电耗（kWh/千瓶）</span>
            </div>
          </template>
          <ChartBox :option="trendOption" grow />
        </PanelBox>

        <PanelBox
          title="近 7 日 × 24 小时产量热力图"
          note="颜色越亮产量越高 · 空格为未上报"
          height="var(--c3)"
        >
          <ChartBox :option="heatmapOption" grow />
        </PanelBox>
      </div>

      <!-- ══════ 右列 ══════ -->
      <div class="col">
        <PanelBox title="今日分时产量与电耗" height="var(--r1)">
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
