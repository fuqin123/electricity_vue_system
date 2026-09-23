/**
 * 后台图表配置工厂（亮色主题）
 * 配色取自源力设计系统 chart-1..5：蓝 / 绿 / 黄 / 红 / 深灰
 */
import type { EChartsOption } from 'echarts';
import type { TrendPoint } from '@eep/shared';

const C = {
  blue: '#1664ff',
  green: '#2a814b',
  amber: '#bd7e00',
  red: '#d7312a',
  slate: '#4e5969',
  axis: '#86909c',
  grid: '#eaedf1',
  text: '#1d2129'
};

const FONT = 'PingFang SC, Microsoft YaHei, sans-serif';

function tip() {
  return {
    trigger: 'axis' as const,
    backgroundColor: '#ffffff',
    borderColor: '#dde2e9',
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: C.text, fontSize: 12, fontFamily: FONT },
    extraCssText: 'box-shadow: 0 5px 15px rgba(12,13,14,.08);border-radius:4px;',
    axisPointer: { type: 'line' as const, lineStyle: { color: '#d1d5da', width: 1 } }
  };
}

function catAxis(labels: string[], interval: number | 'auto' = 'auto') {
  return {
    type: 'category' as const,
    data: labels,
    boundaryGap: true,
    axisLine: { lineStyle: { color: C.grid } },
    axisTick: { show: false },
    axisLabel: { color: C.axis, fontSize: 11, fontFamily: FONT, interval, margin: 10 },
    splitLine: { show: false }
  };
}

function valAxis(name = '') {
  return {
    type: 'value' as const,
    name,
    nameTextStyle: { color: C.axis, fontSize: 11, fontFamily: FONT },
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: C.axis, fontSize: 11, fontFamily: FONT },
    splitLine: { lineStyle: { color: C.grid, type: [3, 4] as number[] } }
  };
}

/**
 * 缩放 + 保留小数位，缺数（null）原样传成 null。
 * 与大屏同一条规矩：null 参加除法会被当成 0，图上就成了「这个周期没用电」（B-513）；
 * 非零值被舍成 0 时自动加精度，避免「23 kWh 在万kWh 轴上显示成 0」。
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

/** 产量柱 + 用电折线（双轴） */
export function outputKwhOption(
  points: TrendPoint[],
  opts: { labelInterval?: number | 'auto'; outDiv?: number; kwhDiv?: number; outUnit?: string; kwhUnit?: string } = {}
): EChartsOption {
  const outDiv = opts.outDiv ?? 10000;
  const kwhDiv = opts.kwhDiv ?? 10000;
  const outUnit = opts.outUnit ?? '万瓶';
  const kwhUnit = opts.kwhUnit ?? '万kWh';

  return {
    animationDuration: 520,
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{ axisValue: string; marker: string; seriesName: string; value: number }>;
        let s = `<b style="color:${C.text}">${arr[0].axisValue}</b><br>`;
        arr.forEach((p) => {
          const u = p.seriesName.includes('产量') ? outUnit : kwhUnit;
          s += `${p.marker}${p.seriesName}：<b>${p.value ?? '--'}</b> ${u}<br>`;
        });
        return s;
      }
    },
    legend: {
      show: true,
      right: 0,
      top: 0,
      itemWidth: 8,
      itemHeight: 8,
      icon: 'roundRect',
      textStyle: { color: C.slate, fontSize: 12, fontFamily: FONT }
    },
    grid: { left: 4, right: 4, top: 34, bottom: 2, containLabel: true },
    xAxis: catAxis(
      points.map((p) => p.label),
      opts.labelInterval ?? 'auto'
    ),
    yAxis: [valAxis(''), { ...valAxis(''), splitLine: { show: false } }],
    series: [
      {
        name: '产量',
        type: 'bar',
        // 产量未接入（null）时保留 null：柱子留空，tooltip 显示 --，不能当 0 画
        data: points.map((p) => scale(p.output, outDiv, 2)),
        barMaxWidth: 22,
        itemStyle: {
          borderRadius: [2, 2, 0, 0],
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: C.blue },
              { offset: 1, color: 'rgba(22,100,255,.35)' }
            ]
          }
        }
      },
      {
        name: '用电量',
        type: 'line',
        yAxisIndex: 1,
        data: points.map((p) => scale(p.kwh, kwhDiv, 3)),
        // 缺数周期之间不连线（拿两端凑中间那段没采集的时段）
        connectNulls: false,
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { width: 2, color: C.amber },
        itemStyle: { color: C.amber, borderColor: '#fff', borderWidth: 1.5 }
      }
    ]
  };
}

/** 单位电耗横向对标（各产线） */
/**
 * 各线单位电耗对标
 *
 * ⚠ `unitKwh` 允许为 null（产量未接入时算不出来）。
 *   这类产线**不参与排序**，只在图末以「--」列出 —— 不能按 0 排序，
 *   否则算不出来的线会排在最前面，看起来像「能效最好的线」。
 */
export function unitKwhCompareOption(
  rows: { lineCode: string; lineName: string; unitKwh: number | null }[],
  target: number
): EChartsOption {
  const sorted = [...rows].sort((a, b) => {
    if (a.unitKwh === null) return 1;
    if (b.unitKwh === null) return -1;
    return a.unitKwh - b.unitKwh;
  });

  return {
    animationDuration: 520,
    tooltip: {
      ...tip(),
      trigger: 'item',
      formatter: (p: unknown) => {
        const d = p as { name: string; value: number | null };
        if (d.value === null || d.value === undefined) {
          return `<b style="color:${C.text}">${d.name}</b><br>单位电耗：<b>--</b>（产量未接入，算不出来）`;
        }
        const diff = ((d.value - target) / target) * 100;
        return (
          `<b style="color:${C.text}">${d.name}</b><br>` +
          `单位电耗：<b>${d.value.toFixed(2)}</b> kWh/千瓶<br>` +
          `目标 ${target.toFixed(2)} · 偏离 <b style="color:${diff > 0 ? C.red : C.green}">${diff > 0 ? '+' : ''}${diff.toFixed(1)}%</b>`
        );
      }
    },
    grid: { left: 4, right: 72, top: 8, bottom: 2, containLabel: true },
    xAxis: { ...valAxis(''), max: (v: { max: number }) => Math.ceil(v.max * 1.15) },
    yAxis: {
      ...catAxis(sorted.map((r) => r.lineName.replace(' 生产线', '')), 0),
      axisLabel: { color: C.slate, fontSize: 12, fontFamily: FONT }
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 16,
        data: sorted.map((r) =>
          r.unitKwh === null
            ? {
                // 产量未接入：给 null（柱不画），标签单独显示「--」
                value: null,
                name: r.lineName,
                label: { show: true, formatter: '--', color: C.slate }
              }
            : {
                value: +r.unitKwh.toFixed(2),
                name: r.lineName,
                itemStyle: {
                  borderRadius: [0, 2, 2, 0],
                  color: r.unitKwh > target ? C.red : C.green
                }
              }
        ),
        label: {
          show: true,
          position: 'right',
          distance: 8,
          color: C.slate,
          fontSize: 12,
          fontFamily: FONT,
          formatter: '{c}'
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: C.blue, type: [4, 4], width: 1.4 },
          data: [{ xAxis: target }],
          label: { formatter: `目标 ${target}`, color: C.blue, fontSize: 11, position: 'end' }
        }
      }
    ]
  };
}

/** 单指标柱状图（用于水耗 / 汽耗等） */
export function simpleBarOption(
  labels: string[],
  values: number[],
  opts: { name: string; unit?: string; color?: string; labelInterval?: number | 'auto' } = { name: '数值' }
): EChartsOption {
  const unit = opts.unit ?? '';
  const color = opts.color ?? C.blue;

  return {
    animationDuration: 520,
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{ axisValue: string; marker: string; seriesName: string; value: number }>;
        return `<b style="color:${C.text}">${arr[0].axisValue}</b><br>${arr[0].marker}${arr[0].seriesName}：<b>${arr[0].value}</b> ${unit}`;
      }
    },
    grid: { left: 4, right: 4, top: 22, bottom: 2, containLabel: true },
    xAxis: catAxis(labels, opts.labelInterval ?? 'auto'),
    yAxis: valAxis(''),
    series: [
      {
        name: opts.name,
        type: 'bar',
        barMaxWidth: 22,
        data: values,
        itemStyle: {
          borderRadius: [2, 2, 0, 0],
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color },
              { offset: 1, color: `rgba(${hexToRgb(color)},.35)` }
            ]
          }
        }
      }
    ]
  };
}

function hexToRgb(hex: string): string {
  const h = hex.replace('#', '');
  return `${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)}`;
}

export const chartColors = C;
