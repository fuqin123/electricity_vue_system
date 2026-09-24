/**
 * charts —— 所有 ECharts 配置工厂
 * 统一深色主题与轴样式，与原设计稿保持一致。
 */
import * as echarts from 'echarts';
import {
  energyModel,
  hexA,
  HOUR_QUALITY_LABEL,
  int as fmtInt,
  lineColors,
  pad2,
  palette as P,
  thresholds,
  PLAN_RATE,
  PROFILE,
  type HourPoint,
  type LineData,
  type TrendDim,
  type TrendPoint,
  type UtilityMetric,
  type WorkshopData
} from '@eep/shared';

const FONT_CN = 'PingFang SC, Microsoft YaHei, sans-serif';
const FONT_NUM = 'JetBrains Mono, monospace';

/* ── 公共样式 ───────────────────────────────────────────── */
function tip() {
  return {
    trigger: 'axis' as const,
    backgroundColor: 'rgba(8,10,38,.94)',
    borderColor: 'rgba(124,140,255,.45)',
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: P.t2, fontSize: 11, fontFamily: FONT_CN },
    axisPointer: { type: 'line' as const, lineStyle: { color: 'rgba(124,140,255,.5)', width: 1 } }
  };
}

function catAxis(data: (string | number)[], opts: { gap?: boolean; interval?: number | 'auto'; rotate?: number } = {}) {
  return {
    type: 'category' as const,
    data,
    boundaryGap: opts.gap !== false,
    axisLine: { lineStyle: { color: 'rgba(255,255,255,.12)' } },
    axisTick: { show: false },
    axisLabel: {
      color: P.t5,
      fontSize: 10,
      fontFamily: FONT_NUM,
      interval: opts.interval === undefined ? ('auto' as const) : opts.interval,
      rotate: opts.rotate ?? 0,
      margin: 10
    },
    splitLine: { show: false }
  };
}

function valAxis(name = '', opts: { min?: number; max?: number; fmt?: (v: number) => string } = {}) {
  return {
    type: 'value' as const,
    name,
    nameTextStyle: { color: P.t5, fontSize: 10, padding: [0, 0, 0, -4] },
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: {
      color: P.t5,
      fontSize: 10,
      fontFamily: FONT_NUM,
      formatter: opts.fmt ?? ((v: number) => String(v))
    },
    splitLine: { lineStyle: { color: P.grid, type: [3, 6] as number[] } },
    min: opts.min,
    max: opts.max
  };
}

function gridBox(o: { left?: number; right?: number; top?: number; bottom?: number } = {}) {
  return {
    left: o.left ?? 8,
    right: o.right ?? 12,
    top: o.top ?? 22,
    bottom: o.bottom ?? 4,
    containLabel: true
  };
}

const grad = (
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  stops: [number, string][]
): echarts.graphic.LinearGradient =>
  new echarts.graphic.LinearGradient(x1, y1, x2, y2, stops.map(([offset, color]) => ({ offset, color })));

/**
 * 缩放 + 保留小数位，**缺数（null）原样传成 null**。
 *
 * ⚠ 两种写法都会出错，而且错得很安静（B-513）：
 *   · `(v / div).toFixed(n)` —— v 为 null 时 JS 把 null 当 0 参加运算，
 *     得到 0，图上就是一根「这个周期没用电」的零高度柱，正是铁律禁止的补 0；
 *   · `v.toFixed(n)` —— v 为 null 时抛 TypeError，
 *     整个 computed 崩掉，**一整张趋势图直接不渲染**。
 * 传 null 给 ECharts 才是正确表达：柱子留空、折线断开。
 *
 * 非零值被舍成 0 时自动加精度（同 B-101 的教训）：
 * 单线一年只用 23 kWh，在「万kWh」轴上按 2 位小数会显示成 0，
 * 读起来就是「这一年没用电」—— 量级正常、没有报错的假数字最危险。
 */
function scale(v: number | null | undefined, div: number, digits: number): number | null {
  if (v === null || v === undefined || Number.isNaN(v)) return null;
  if (v === 0) return 0;
  for (let d = digits; d <= digits + 6; d++) {
    const s = +(v / div).toFixed(d);
    if (s !== 0) return s;
  }
  return +(v / div).toFixed(digits + 6);
}

/**
 * 趋势图各维度的取数与显示单位。
 *
 * ⚠ 除数与单位文案必须同源：之前图例在模板里写死「万瓶 / 万kWh」，
 *   而「年」维度实际按亿出数、「时」维度按千出数 —— 图例和 tooltip 互相打脸（B-514）。
 *   模板一律读本表，不要再各写一套。
 */
export interface TrendUnits {
  outDiv: number;
  outUnit: string;
  kwhDiv: number;
  kwhUnit: string;
  digits: number;
}

/** 车间总览（量级大） */
export const OVERVIEW_TREND: Record<TrendDim, TrendUnits> = {
  年: { outDiv: 1e8, outUnit: '亿瓶', kwhDiv: 1e8, kwhUnit: '亿kWh', digits: 2 },
  月: { outDiv: 1e4, outUnit: '万瓶', kwhDiv: 1e4, kwhUnit: '万kWh', digits: 2 },
  日: { outDiv: 1e4, outUnit: '万瓶', kwhDiv: 1e4, kwhUnit: '万kWh', digits: 2 },
  时: { outDiv: 1e3, outUnit: '千瓶', kwhDiv: 1e3, kwhUnit: '千kWh', digits: 2 }
};

/** 单线（量级比车间小得多，电量用 kWh / 万kWh） */
export const LINE_TREND: Record<TrendDim, TrendUnits> = {
  年: { outDiv: 1e8, outUnit: '亿瓶', kwhDiv: 1e4, kwhUnit: '万kWh', digits: 2 },
  月: { outDiv: 1e4, outUnit: '万瓶', kwhDiv: 1e4, kwhUnit: '万kWh', digits: 2 },
  日: { outDiv: 1e4, outUnit: '万瓶', kwhDiv: 1, kwhUnit: 'kWh', digits: 2 },
  时: { outDiv: 1e3, outUnit: '千瓶', kwhDiv: 1, kwhUnit: 'kWh', digits: 2 }
};

/** 取某维度的数据源；「时」= 当日逐小时，其余 = 周期总计。序列一律时间正序，末项是本期。 */
type TrendRow = { label?: string; output: number | null; kwh: number | null; unitKwh?: number | null };
function pickRows(
  scope: { hours: HourPoint[]; daily30: TrendPoint[]; monthly12: TrendPoint[]; yearly5: TrendPoint[] },
  dim: TrendDim
): TrendRow[] {
  if (dim === '时') return scope.hours;
  if (dim === '日') return scope.daily30;
  if (dim === '月') return scope.monthly12;
  return scope.yearly5;
}
function pickLabels(scope: { hours: HourPoint[] }, dim: TrendDim, rows: TrendRow[]): string[] {
  if (dim === '时') return scope.hours.map((x) => `${pad2(x.hour)}:00`);
  return rows.map((x) => x.label ?? '');
}

/* ══════════════════════════════════════════════════════════
   大屏一 · 产量与用电趋势（年 / 月 / 日 / 时）
   ══════════════════════════════════════════════════════════ */
export function overviewTrendOption(ws: WorkshopData, dim: TrendDim): echarts.EChartsOption {
  const t = OVERVIEW_TREND[dim];
  const rows = pickRows(ws, dim);
  const labels = pickLabels(ws, dim, rows);
  const out = rows.map((x) => scale(x.output, t.outDiv, t.digits));
  const kwh = rows.map((x) => scale(x.kwh, t.kwhDiv, t.digits));
  const uq = t.outUnit;
  const eq = t.kwhUnit;

  return {
    animationDuration: 600,
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{ axisValue: string; marker: string; seriesName: string; value: number | null }>;
        let s = `<b style="color:#c9d0f5;font-weight:500">${arr[0].axisValue}</b><br>`;
        arr.forEach((p) => {
          const u = p.seriesName.includes('产量') ? uq : eq;
          s += `${p.marker}${p.seriesName}：<b style="font-family:monospace">${p.value ?? '--'}</b> ${u}<br>`;
        });
        return s;
      }
    },
    legend: { show: false },
    grid: gridBox({ top: 26, left: 4, right: 6, bottom: 2 }),
    xAxis: catAxis(labels, { interval: labels.length > 24 ? 2 : 0 }),
    yAxis: [valAxis('', { min: 0 }), { ...valAxis('', { min: 0 }), splitLine: { show: false } }],
    series: [
      {
        name: '产量',
        type: 'bar',
        data: out,
        yAxisIndex: 0,
        barMaxWidth: 26,
        barGap: '10%',
        itemStyle: {
          borderRadius: [3, 3, 0, 0],
          color: grad(0, 1, 0, 0, [
            [0, hexA(P.primary, 0.45)],
            [1, P.cyan]
          ])
        }
      },
      {
        name: '用电量',
        type: 'line',
        data: kwh,
        yAxisIndex: 1,
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { width: 2, color: P.magenta, shadowColor: hexA(P.magenta, 0.55), shadowBlur: 8 },
        itemStyle: { color: P.magenta, borderColor: '#0a0d3a', borderWidth: 1.5 },
        connectNulls: false,
        areaStyle: {
          color: grad(0, 0, 0, 1, [
            [0, hexA(P.magenta, 0.22)],
            [1, hexA(P.magenta, 0)]
          ])
        }
      }
    ]
  };
}

/* ══════════════════════════════════════════════════════════
   大屏一 · 六条产线产量与单位电耗对比
   ══════════════════════════════════════════════════════════ */
/**
 * 大屏一 · 六条产线横向对比
 *
 * ⚠ 与单线趋势图同一套自适应口径（ADR-013）：
 *   · 产量已接入 → 条长＝今日产量（万瓶），右侧标单位电耗；
 *   · 产量未接入 → 条长＝今日用电量（kWh），右侧标「用电量 · 占车间合计比例」。
 * 原来只画产量：产量为 null 时柱子长度 0，**右侧标签也随柱子一起不渲染**，
 * 于是 958px 宽的面板只有最左边 40px 有线名，看起来像「图没对齐边界 / 图坏了」（B-515）。
 * 缺数时给「--」而不是把整条抹掉。
 */
export function overviewLinesCompareOption(ws: WorkshopData): echarts.EChartsOption {
  const byOutput = ws.lines.some((l) => l.today.output !== null);
  /** 条长用的原始值 */
  const raw = (l: LineData): number | null => (byOutput ? l.today.output : l.today.kwh);
  const ls = [...ws.lines].sort((a, b) => (raw(a) ?? 0) - (raw(b) ?? 0));
  const cats = ls.map((l) => `${l.id} 线`);
  /** 车间合计（只累加有可信值的线），用于「占车间」 */
  const kwhTotal = ws.lines.reduce((a, l) => a + (l.today.kwh ?? 0), 0);
  const data = ls.map((l) => {
    const v = raw(l);
    return {
      value: byOutput ? scale(v, 10000, 2) : scale(v, 1, 0),
      unitKwh: l.today.unitKwh,
      rate: l.today.rate,
      plan: scale(l.today.plan, 10000, 2),
      kwh: scale(l.today.kwh, 1, 0),
      share: kwhTotal > 0 && l.today.kwh !== null ? +((l.today.kwh / kwhTotal) * 100).toFixed(1) : null,
      color: lineColors[l.id]
    };
  });
  type Row = (typeof data)[number];

  return {
    animationDuration: 600,
    tooltip: {
      ...tip(),
      trigger: 'item',
      formatter: (p: unknown) => {
        const d = (p as { data: Row }).data;
        const n = (v: number | null, u = '') => `${v ?? '--'}${u}`;
        return (
          `<b style="color:#fff">${(p as { name: string }).name}</b><br>` +
          (byOutput
            ? `今日产量：<b style="font-family:monospace">${n(d.value, ' 万瓶')}</b><br>` +
              `计划产量：<b style="font-family:monospace">${n(d.plan, ' 万瓶')}</b><br>` +
              `达成率：<b style="font-family:monospace">${d.rate}%</b><br>`
            : `今日用电：<b style="font-family:monospace">${n(d.kwh, ' kWh')}</b><br>` +
              `占车间合计：<b style="font-family:monospace">${n(d.share, '%')}</b><br>` +
              `今日产量：<b style="font-family:monospace">--</b>（产量未接入）<br>`) +
          `单位电耗：<b style="font-family:monospace">${d.unitKwh === null ? '--' : d.unitKwh.toFixed(2)}</b> kWh/千瓶`
        );
      }
    },
    // 右侧要留够：最长标签「52.75 万瓶  ·  7.71 kWh/千瓶」约 155px，
    // 原来只留 132px，标签会被画布右边缘裁掉（同 B-515 的「没对齐边界」）
    grid: gridBox({ left: 4, right: 170, top: 12, bottom: 2 }),
    xAxis: valAxis('', { min: 0 }),
    yAxis: {
      ...catAxis(cats, { gap: true }),
      axisLabel: { color: P.t2, fontSize: 11, fontFamily: FONT_CN }
    },
    series: [
      {
        type: 'bar',
        data,
        barMaxWidth: 14,
        itemStyle: {
          borderRadius: [0, 7, 7, 0],
          color: (p: unknown) => {
            const c = (p as { data: Row }).data.color;
            return grad(0, 0, 1, 0, [
              [0, hexA(c, 0.3)],
              [1, c]
            ]);
          }
        },
        label: {
          show: true,
          position: 'right',
          distance: 8,
          formatter: (p: unknown) => {
            const d = (p as { data: Row }).data;
            const u = d.unitKwh === null ? '--' : d.unitKwh.toFixed(2);
            const over = d.unitKwh !== null && d.unitKwh > thresholds.unitKwhHigh;
            if (byOutput) {
              return `{a|${d.value ?? '--'} 万瓶}{b|  ·  }{${over ? 'e' : 'c'}|${u}}{d| kWh/千瓶}`;
            }
            return `{a|${d.kwh ?? '--'} kWh}{b|  ·  }{c|${d.share ?? '--'}}{d| % 车间}`;
          },
          rich: {
            a: { color: '#e8ecff', fontSize: 12, fontFamily: FONT_NUM },
            b: { color: '#5f6ba8', fontSize: 11 },
            c: { color: P.cyan, fontSize: 11, fontFamily: FONT_NUM },
            d: { color: '#5f6ba8', fontSize: 10 },
            e: { color: P.amber, fontSize: 11, fontFamily: FONT_NUM }
          }
        }
      }
    ]
  };
}

/* ══════════════════════════════════════════════════════════
   今日分时产量与电耗（已产出实心 / 未到时段描边）
   ══════════════════════════════════════════════════════════ */
export function todayChartOption(hours: HourPoint[], ratedTotal: number, currentHour: number): echarts.EChartsOption {
  const cats = hours.map((x) => pad2(x.hour));
  const out = hours.map((x, i) => {
    if (x.future) {
      const plan = Math.round(ratedTotal * PLAN_RATE * PROFILE[i]);
      return {
        value: +(plan / 1000).toFixed(1),
        itemStyle: {
          color: 'rgba(88,101,242,.08)',
          borderColor: 'rgba(88,101,242,.42)',
          borderWidth: 1,
          borderType: [3, 3] as number[],
          borderRadius: [3, 3, 0, 0] as number[]
        }
      };
    }
    // 产量未接入（null）时柱子留空（null），不能画成 0 高度的实心柱
    return { value: x.output === null ? null : +(x.output / 1000).toFixed(1) };
  });
  const kwh = hours.map((x) => (x.kwh === null ? null : +(x.kwh / 1000).toFixed(2)));

  return {
    animationDuration: 600,
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{ dataIndex: number; marker: string; seriesName: string; value: number | null }>;
        const idx = arr[0].dataIndex;
        const h = hours[idx];
        /**
         * 状态后缀与「本线小时明细」同一套判定：进行中只看是不是当前整点，
         * 已结束的异常小时必须写异常，不能因为 `partial` 就写进行中（B-518）。
         */
        const state = h.future
          ? '计划'
          : h.hour === currentHour
            ? '进行中'
            : h.quality && h.quality !== 'ok'
              ? (HOUR_QUALITY_LABEL[h.quality] ?? '异常')
              : '';
        let head =
          `<b style="color:#c9d0f5">${cats[idx]} 时</b>` +
          (state ? `<span style="color:#5f6ba8"> · ${state}</span>` : '') +
          '<br>';
        arr.forEach((p) => {
          if (p.value === null || p.value === undefined) return;
          const u = p.seriesName.includes('产量') ? '千瓶' : '千kWh';
          head += `${p.marker}${p.seriesName}：<b style="font-family:monospace">${p.value}</b> ${u}<br>`;
        });
        return head;
      }
    },
    grid: gridBox({ top: 26, left: 2, right: 4, bottom: 2 }),
    xAxis: catAxis(cats, { interval: 1 }),
    yAxis: [valAxis('', { min: 0 }), { ...valAxis('', { min: 0 }), splitLine: { show: false } }],
    series: [
      {
        name: '产量',
        type: 'bar',
        data: out,
        yAxisIndex: 0,
        barMaxWidth: 11,
        itemStyle: {
          borderRadius: [3, 3, 0, 0],
          color: grad(0, 1, 0, 0, [
            [0, hexA(P.primary, 0.35)],
            [1, P.cyan]
          ])
        }
      },
      {
        name: '电耗',
        type: 'line',
        data: kwh,
        yAxisIndex: 1,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        showSymbol: false,
        lineStyle: { width: 1.8, color: P.amber, shadowColor: hexA(P.amber, 0.5), shadowBlur: 6 },
        itemStyle: { color: P.amber },
        connectNulls: false
      }
    ]
  };
}

/* ══════════════════════════════════════════════════════════
   水/汽 车间总表白
   ══════════════════════════════════════════════════════════ */
export function utilitySparkOption(util: { water: UtilityMetric; steam: UtilityMetric }): echarts.EChartsOption {
  const cats: string[] = [];
  for (let i = 0; i < 24; i++) cats.push(pad2(i));

  return {
    animationDuration: 500,
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{ axisValue: string; marker: string; seriesName: string; value: number | null }>;
        let s = `<b style="color:#c9d0f5">${arr[0].axisValue} 时</b><br>`;
        arr.forEach((p) => {
          if (p.value === null || p.value === undefined) return;
          s += `${p.marker}${p.seriesName}：<b style="font-family:monospace">${p.value}</b> t/h<br>`;
        });
        return s;
      }
    },
    grid: { left: 4, right: 4, top: 8, bottom: 2, containLabel: true },
    xAxis: catAxis(cats, { interval: 5, gap: false }),
    yAxis: valAxis('t/h', { min: 0 }),
    series: [
      {
        name: '水',
        type: 'line',
        data: util.water.trend,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.8, color: P.cyan },
        areaStyle: {
          color: grad(0, 0, 0, 1, [
            [0, hexA(P.cyan, 0.28)],
            [1, hexA(P.cyan, 0)]
          ])
        }
      },
      {
        name: '汽',
        type: 'line',
        data: util.steam.trend,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.8, color: P.magenta },
        areaStyle: {
          color: grad(0, 0, 0, 1, [
            [0, hexA(P.magenta, 0.22)],
            [1, hexA(P.magenta, 0)]
          ])
        }
      }
    ]
  };
}

/* ══════════════════════════════════════════════════════════
   大屏二 · 单线趋势（年 / 月 / 日 / 时，与总览同口径可切换）
   ══════════════════════════════════════════════════════════ */

/** 单位电耗轴范围：围绕实际值收紧，避免趋势被压平 */
function unitKwhRange(uq: (number | null)[]): { min: number; max: number } {
  const valid = uq.filter((v): v is number => v !== null && v !== undefined);
  if (!valid.length) return { min: 0, max: 20 };
  let min = Math.max(0, Math.floor(Math.min(...valid) * 10 - 4) / 10);
  let max = Math.ceil(Math.max(...valid) * 10 + 6) / 10;
  if (max - min < 2) {
    min = Math.max(0, min - 1);
    max += 1;
  }
  return { min, max };
}

/**
 * 单线产量与用能趋势（可切换年 / 月 / 日 / 时）
 *
 * 两条曲线是**自适应**的，取决于单位电耗是否算得出来：
 *   · 产量已接入（unitKwh 有值）→ 产量 + 单位电耗，带目标线，用于能效考核
 *   · 产量未接入（unitKwh 全为 null）→ 产量 + 用电量，否则右边那条会是空线
 * 这样在产量接口接入前后，图表都始终有两条有意义的曲线。
 */
export function lineTrendOption(line: LineData, dim: TrendDim): echarts.EChartsOption {
  const t = LINE_TREND[dim];
  const rows = pickRows(line, dim);
  const labels = pickLabels(line, dim, rows);
  const out = rows.map((x) => scale(x.output, t.outDiv, t.digits));
  const kwh = rows.map((x) => scale(x.kwh, t.kwhDiv, t.digits));
  const uq: (number | null)[] = rows.map((x) => x.unitKwh ?? null);
  /** 产量与用电量的显示单位：单线量级比车间小得多，每个维度用各自的合适单位 */
  const outUnit = t.outUnit;
  const kwhUnit = t.kwhUnit;
  const hasUnitKwh = uq.some((v) => v !== null && v !== undefined);
  const target = energyModel.targetUnitKwh;
  const range = unitKwhRange(uq);

  /** 第二条曲线的名称 / 单位 / 数据（自适应） */
  const secondName = hasUnitKwh ? '单位电耗' : '用电量';
  const secondUnit = hasUnitKwh ? 'kWh/千瓶' : kwhUnit;
  const secondData = hasUnitKwh ? uq : kwh;

  return {
    animationDuration: 600,
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{ axisValue: string; marker: string; seriesName: string; value: number | null }>;
        let s = `<b style="color:#c9d0f5">${arr[0].axisValue}</b><br>`;
        arr.forEach((p) => {
          if (p.value === null || p.value === undefined) return;
          const u = p.seriesName.includes('产量') ? outUnit : secondUnit;
          s += `${p.marker}${p.seriesName}：<b style="font-family:monospace">${p.value}</b> ${u}<br>`;
        });
        return s;
      }
    },
    grid: gridBox({ top: 28, left: 4, right: 6, bottom: 2 }),
    xAxis: catAxis(labels, { interval: labels.length > 24 ? 2 : 4 }),
    yAxis: [
      valAxis('', { min: 0 }),
      {
        ...valAxis('', hasUnitKwh ? { min: range.min, max: range.max } : { min: 0 }),
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: '产量',
        type: hasUnitKwh ? 'line' : 'bar',
        data: out,
        yAxisIndex: 0,
        // 缺数的周期之间不许连线：连过去就是拿两端凑出中间那段没有采集的时段
        connectNulls: false,
        barMaxWidth: 26,
        smooth: 0.35,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { width: 2.4, color: P.cyan, shadowColor: hexA(P.cyan, 0.55), shadowBlur: 10 },
        itemStyle: hasUnitKwh
          ? { color: P.cyan, borderColor: '#0a0d3a', borderWidth: 1.5 }
          : {
              borderRadius: [3, 3, 0, 0],
              color: grad(0, 1, 0, 0, [
                [0, hexA(P.primary, 0.45)],
                [1, P.cyan]
              ])
            },
        areaStyle: hasUnitKwh
          ? {
              color: grad(0, 0, 0, 1, [
                [0, hexA(P.cyan, 0.34)],
                [0.55, hexA(P.primary, 0.12)],
                [1, hexA(P.primary, 0)]
              ])
            }
          : undefined
      },
      {
        name: secondName,
        type: 'line',
        data: secondData,
        yAxisIndex: 1,
        connectNulls: false,
        smooth: 0.35,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { width: 1.8, color: P.amber, type: hasUnitKwh ? [6, 4] : 'solid' },
        itemStyle: { color: P.amber, borderColor: '#0a0d3a', borderWidth: 1.5 },
        // 与总览一致：折线下方给一层渐隐阴影。
        // 只在第二条曲线是「用电量」时画 —— 是「单位电耗」时它是虚线参考线，
        // 再铺一层面积会把产量曲线下面的空间糊住。
        areaStyle: hasUnitKwh
          ? undefined
          : {
              color: grad(0, 0, 0, 1, [
                [0, hexA(P.amber, 0.22)],
                [1, hexA(P.amber, 0)]
              ])
            },
        markLine: hasUnitKwh
          ? {
              silent: true,
              symbol: 'none',
              lineStyle: { color: hexA(P.green, 0.55), type: [4, 4], width: 1.4 },
              data: [{ yAxis: target, name: '目标' }],
              label: { color: P.green, fontSize: 9, position: 'insideEndTop', formatter: `目标 ${target}` }
            }
          : undefined
      }
    ]
  };
}

/* ══════════════════════════════════════════════════════════
   大屏二 · 近 7 日 × 24 小时产量热力图
   ══════════════════════════════════════════════════════════ */
export function lineHeatmapOption(line: LineData): echarts.EChartsOption {
  const hours: string[] = [];
  for (let i = 0; i < 24; i++) hours.push(pad2(i));
  const daysAsc = [...line.heatDays].reverse(); // ECharts y 轴自下而上
  const data: [number, number, number][] = [];
  let max = 0;
  line.heat.forEach((row, dayIdx) => {
    const y = line.heat.length - 1 - dayIdx;
    row.forEach((v, h) => {
      if (v === null) return;
      data.push([h, y, v]);
      if (v > max) max = v;
    });
  });

  return {
    animationDuration: 500,
    tooltip: {
      ...tip(),
      trigger: 'item',
      formatter: (p: unknown) => {
        const v = (p as { value: [number, number, number] }).value;
        return (
          `<b style="color:#c9d0f5">${daysAsc[v[1]]}  ${pad2(v[0])}:00</b><br>` +
          `产量：<b style="font-family:monospace">${fmtInt(v[2] * 100)}</b> 瓶`
        );
      }
    },
    grid: { left: 4, right: 4, top: 8, bottom: 26, containLabel: true },
    xAxis: {
      ...catAxis(hours, { gap: true }),
      splitArea: { show: false },
      axisLabel: { color: P.t5, fontSize: 9, interval: 2, fontFamily: FONT_NUM }
    },
    yAxis: {
      ...catAxis(daysAsc, { gap: true }),
      splitArea: { show: false },
      axisLabel: { color: P.t3, fontSize: 10, fontFamily: FONT_NUM }
    },
    visualMap: {
      min: 0,
      max: Math.max(max, 1),
      calculable: false,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      itemWidth: 10,
      itemHeight: 90,
      textStyle: { color: P.t5, fontSize: 9, fontFamily: FONT_NUM },
      text: ['高', '低'],
      inRange: { color: ['#141a49', '#2a3690', '#5865f2', '#00b0f4', '#7fe3ff'] }
    },
    series: [
      {
        type: 'heatmap',
        data,
        itemStyle: { borderColor: 'rgba(4,6,31,.85)', borderWidth: 1.5, borderRadius: 2 },
        emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 1.5 } },
        progressive: 0
      }
    ]
  };
}
