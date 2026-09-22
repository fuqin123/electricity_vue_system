/**
 * charts —— 所有 ECharts 配置工厂
 * 统一深色主题与轴样式，与原设计稿保持一致。
 */
import * as echarts from 'echarts';
import {
  energyModel,
  hexA,
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

/* ══════════════════════════════════════════════════════════
   大屏一 · 产量与用电趋势（年 / 月 / 日 / 时）
   ══════════════════════════════════════════════════════════ */
export function overviewTrendOption(ws: WorkshopData, dim: TrendDim): echarts.EChartsOption {
  let labels: string[] = [];
  let out: (number | null)[] = [];
  let kwh: (number | null)[] = [];
  let uq = '万瓶';
  let eq = '万kWh';

  if (dim === '时') {
    labels = ws.hours.map((x) => `${pad2(x.hour)}:00`);
    out = ws.hours.map((x) => (x.output === null ? null : +(x.output / 1000).toFixed(1)));
    kwh = ws.hours.map((x) => (x.kwh === null ? null : +(x.kwh / 1000).toFixed(2)));
    uq = '千瓶';
    eq = '千kWh';
  } else if (dim === '日') {
    labels = ws.daily30.map((x) => x.label);
    out = ws.daily30.map((x) => +(x.output / 10000).toFixed(2));
    kwh = ws.daily30.map((x) => +(x.kwh / 10000).toFixed(3));
  } else if (dim === '年') {
    labels = ws.yearly5.map((x) => x.label);
    out = ws.yearly5.map((x) => +(x.output / 100000000).toFixed(3));
    kwh = ws.yearly5.map((x) => +(x.kwh / 100000000).toFixed(4));
    uq = '亿瓶';
    eq = '亿kWh';
  } else {
    labels = ws.monthly12.map((x) => x.label);
    out = ws.monthly12.map((x) => +(x.output / 10000).toFixed(1));
    kwh = ws.monthly12.map((x) => +(x.kwh / 10000).toFixed(2));
  }

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
export function overviewLinesCompareOption(ws: WorkshopData): echarts.EChartsOption {
  const ls = [...ws.lines].sort((a, b) => a.today.output - b.today.output);
  const cats = ls.map((l) => `${l.id} 线`);
  const data = ls.map((l) => ({
    value: +(l.today.output / 10000).toFixed(2),
    unitKwh: l.today.unitKwh,
    rate: l.today.rate,
    plan: +(l.today.plan / 10000).toFixed(2),
    color: lineColors[l.id]
  }));

  return {
    animationDuration: 600,
    tooltip: {
      ...tip(),
      trigger: 'item',
      formatter: (p: unknown) => {
        const d = (p as { data: (typeof data)[number] }).data;
        return (
          `<b style="color:#fff">${(p as { name: string }).name}</b><br>` +
          `今日产量：<b style="font-family:monospace">${d.value}</b> 万瓶<br>` +
          `计划产量：<b style="font-family:monospace">${d.plan}</b> 万瓶<br>` +
          `达成率：<b style="font-family:monospace">${d.rate}%</b><br>` +
          `单位电耗：<b style="font-family:monospace">${d.unitKwh === null ? '--' : d.unitKwh.toFixed(2)}</b> kWh/千瓶`
        );
      }
    },
    grid: gridBox({ left: 4, right: 132, top: 12, bottom: 2 }),
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
            const c = (p as { data: (typeof data)[number] }).data.color;
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
            const d = (p as { data: (typeof data)[number] }).data;
            const u = d.unitKwh === null ? '--' : d.unitKwh.toFixed(2);
            const over = d.unitKwh !== null && d.unitKwh > thresholds.unitKwhHigh;
            return `{a|${d.value} 万瓶}{b|  ·  }{${over ? 'e' : 'c'}|${u}}{d| kWh/千瓶}`;
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
export function todayChartOption(hours: HourPoint[], ratedTotal: number): echarts.EChartsOption {
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
    return { value: +((x.output ?? 0) / 1000).toFixed(1) };
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
        let head =
          `<b style="color:#c9d0f5">${cats[idx]} 时</b>` +
          (h.future
            ? '<span style="color:#5f6ba8"> · 计划</span>'
            : h.partial
              ? '<span style="color:#5f6ba8"> · 进行中</span>'
              : '') +
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
   大屏二 · 单线产量与单位电耗趋势（近 30 日，双轴）
   ══════════════════════════════════════════════════════════ */
export function lineTrendOption(line: LineData): echarts.EChartsOption {
  const cats = line.daily30.map((x) => x.label);
  const out = line.daily30.map((x) => +(x.output / 10000).toFixed(2));
  const uq = line.daily30.map((x) => x.unitKwh ?? null);
  const target = energyModel.targetUnitKwh;

  /* 单位电耗轴范围：围绕实际值收紧，避免趋势被压平 */
  const uqValid = uq.filter((v): v is number => v !== null && v !== undefined);
  let uMin = 0;
  let uMax = 20;
  if (uqValid.length) {
    uMin = Math.max(0, Math.floor(Math.min(...uqValid) * 10 - 4) / 10);
    uMax = Math.ceil(Math.max(...uqValid) * 10 + 6) / 10;
    if (uMax - uMin < 2) {
      uMin = Math.max(0, uMin - 1);
      uMax += 1;
    }
  }

  return {
    animationDuration: 600,
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{ axisValue: string; marker: string; seriesName: string; value: number | null }>;
        let s = `<b style="color:#c9d0f5">${arr[0].axisValue}</b><br>`;
        arr.forEach((p) => {
          if (p.value === null || p.value === undefined) return;
          const u = p.seriesName.includes('产量') ? '万瓶' : 'kWh/千瓶';
          s += `${p.marker}${p.seriesName}：<b style="font-family:monospace">${p.value}</b> ${u}<br>`;
        });
        return s;
      }
    },
    grid: gridBox({ top: 28, left: 4, right: 6, bottom: 2 }),
    xAxis: catAxis(cats, { interval: 4 }),
    yAxis: [valAxis('', { min: 0 }), { ...valAxis('', { min: uMin, max: uMax }), splitLine: { show: false } }],
    series: [
      {
        name: '日产量',
        type: 'line',
        data: out,
        yAxisIndex: 0,
        smooth: 0.35,
        symbol: 'circle',
        symbolSize: 5,
        showSymbol: false,
        lineStyle: { width: 2.4, color: P.cyan, shadowColor: hexA(P.cyan, 0.55), shadowBlur: 10 },
        itemStyle: { color: P.cyan, borderColor: '#0a0d3a', borderWidth: 1.5 },
        areaStyle: {
          color: grad(0, 0, 0, 1, [
            [0, hexA(P.cyan, 0.34)],
            [0.55, hexA(P.primary, 0.12)],
            [1, hexA(P.primary, 0)]
          ])
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: 'rgba(255,255,255,.18)', type: [3, 5] },
          data: [{ type: 'average', name: '均值' }],
          label: { color: P.t5, fontSize: 9, formatter: '均值 {c}' }
        }
      },
      {
        name: '单位电耗',
        type: 'line',
        data: uq,
        yAxisIndex: 1,
        smooth: 0.35,
        symbol: 'circle',
        symbolSize: 4,
        showSymbol: false,
        lineStyle: { width: 1.8, color: P.amber, type: [6, 4] },
        itemStyle: { color: P.amber },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: hexA(P.green, 0.55), type: [4, 4], width: 1.4 },
          data: [{ yAxis: target, name: '目标' }],
          label: { color: P.green, fontSize: 9, position: 'insideEndTop', formatter: `目标 ${target}` }
        }
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
