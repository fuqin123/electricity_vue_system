/**
 * 后台图表配置工厂（亮色主题）
 * 配色取自源力设计系统 chart-1..5：蓝 / 绿 / 黄 / 红 / 深灰
 */
import type { EChartsOption } from "echarts";
import type { TrendPoint } from "@eep/shared";

const C = {
  blue: "#1664ff",
  green: "#2a814b",
  amber: "#bd7e00",
  red: "#d7312a",
  slate: "#4e5969",
  axis: "#86909c",
  grid: "#eaedf1",
  text: "#1d2129",
};

const FONT = "PingFang SC, Microsoft YaHei, sans-serif";

function tip() {
  return {
    trigger: "axis" as const,
    backgroundColor: "#ffffff",
    borderColor: "#dde2e9",
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: C.text, fontSize: 12, fontFamily: FONT },
    extraCssText:
      "box-shadow: 0 5px 15px rgba(12,13,14,.08);border-radius:4px;",
    axisPointer: {
      type: "line" as const,
      lineStyle: { color: "#d1d5da", width: 1 },
    },
  };
}

function catAxis(labels: string[], interval: number | "auto" = "auto") {
  return {
    type: "category" as const,
    data: labels,
    boundaryGap: true,
    axisLine: { lineStyle: { color: C.grid } },
    axisTick: { show: false },
    axisLabel: {
      color: C.axis,
      fontSize: 11,
      fontFamily: FONT,
      interval,
      margin: 10,
    },
    splitLine: { show: false },
  };
}

function valAxis(name = "") {
  return {
    type: "value" as const,
    name,
    nameTextStyle: { color: C.axis, fontSize: 11, fontFamily: FONT },
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: C.axis, fontSize: 11, fontFamily: FONT },
    splitLine: { lineStyle: { color: C.grid, type: [3, 4] as number[] } },
  };
}

/**
 * 缩放 + 保留小数位，缺数（null）原样传成 null。
 * 与大屏同一条规矩：null 参加除法会被当成 0，图上就成了「这个周期没用电」（B-513）；
 * 非零值被舍成 0 时自动加精度，避免「23 kWh 在万kWh 轴上显示成 0」。
 */
function scale(
  v: number | null | undefined,
  div: number,
  digits: number,
): number | null {
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
  opts: {
    labelInterval?: number | "auto";
    outDiv?: number;
    kwhDiv?: number;
    outUnit?: string;
    kwhUnit?: string;
  } = {},
): EChartsOption {
  const outDiv = opts.outDiv ?? 10000;
  const kwhDiv = opts.kwhDiv ?? 10000;
  const outUnit = opts.outUnit ?? "万瓶";
  const kwhUnit = opts.kwhUnit ?? "万kWh";

  return {
    animationDuration: 520,
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{
          axisValue: string;
          marker: string;
          seriesName: string;
          value: number;
        }>;
        let s = `<b style="color:${C.text}">${arr[0].axisValue}</b><br>`;
        arr.forEach((p) => {
          const u = p.seriesName.includes("产量") ? outUnit : kwhUnit;
          s += `${p.marker}${p.seriesName}：<b>${p.value ?? "--"}</b> ${u}<br>`;
        });
        return s;
      },
    },
    legend: {
      show: true,
      right: 0,
      top: 0,
      itemWidth: 8,
      itemHeight: 8,
      icon: "roundRect",
      textStyle: { color: C.slate, fontSize: 12, fontFamily: FONT },
    },
    grid: { left: 4, right: 4, top: 34, bottom: 2, containLabel: true },
    xAxis: catAxis(
      points.map((p) => p.label),
      opts.labelInterval ?? "auto",
    ),
    yAxis: [valAxis(""), { ...valAxis(""), splitLine: { show: false } }],
    series: [
      {
        name: "产量",
        type: "bar",
        // 产量未接入（null）时保留 null：柱子留空，tooltip 显示 --，不能当 0 画
        data: points.map((p) => scale(p.output, outDiv, 2)),
        barMaxWidth: 22,
        itemStyle: {
          borderRadius: [2, 2, 0, 0],
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: C.blue },
              { offset: 1, color: "rgba(22,100,255,.35)" },
            ],
          },
        },
      },
      {
        name: "用电量",
        type: "line",
        yAxisIndex: 1,
        data: points.map((p) => scale(p.kwh, kwhDiv, 3)),
        // 缺数周期之间不连线（拿两端凑中间那段没采集的时段）
        connectNulls: false,
        smooth: true,
        symbol: "circle",
        symbolSize: 5,
        lineStyle: { width: 2, color: C.amber },
        itemStyle: { color: C.amber, borderColor: "#fff", borderWidth: 1.5 },
      },
    ],
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
  rows: { lineCode: string; lineName: string; unitKwh: number | null; standardUnitKwh?: number | null }[],
  target: number | null,
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
      trigger: "item",
      formatter: (p: unknown) => {
        const d = p as { name: string; value: number | null; data?: { standard?: number | null } };
        if (d.value === null || d.value === undefined) {
          return `<b style="color:${C.text}">${d.name}</b><br>单位电耗：<b>--</b>（产量未接入，算不出来）`;
        }
        const std = d.data?.standard ?? target;
        if (std === null || std === undefined) {
          return (
            `<b style="color:${C.text}">${d.name}</b><br>` +
            `单位电耗：<b>${d.value.toFixed(2)}</b> kWh/千瓶<br>` +
            `标准单耗未标定（后台「能源配置管理 → 标准单耗配置」维护）`
          );
        }
        const diff = ((d.value - std) / std) * 100;
        return (
          `<b style="color:${C.text}">${d.name}</b><br>` +
          `单位电耗：<b>${d.value.toFixed(2)}</b> kWh/千瓶<br>` +
          `标准 ${std.toFixed(2)} · 偏离 <b style="color:${diff > 0 ? C.red : C.green}">${diff > 0 ? "+" : ""}${diff.toFixed(1)}%</b>`
        );
      },
    },
    grid: { left: 4, right: 72, top: 8, bottom: 2, containLabel: true },
    xAxis: {
      ...valAxis(""),
      max: (v: { max: number }) => Math.ceil(v.max * 1.15),
    },
    yAxis: {
      ...catAxis(
        sorted.map((r) => r.lineName.replace(" 生产线", "")),
        0,
      ),
      axisLabel: { color: C.slate, fontSize: 12, fontFamily: FONT },
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 16,
        data: sorted.map((r) => {
          const std = r.standardUnitKwh ?? target;
          return r.unitKwh === null
            ? {
                // 产量未接入：给 null（柱不画），标签单独显示「--」
                value: null,
                name: r.lineName,
                standard: std,
                label: { show: true, formatter: "--", color: C.slate },
              }
            : {
                value: +r.unitKwh.toFixed(2),
                name: r.lineName,
                standard: std,
                itemStyle: {
                  borderRadius: [0, 2, 2, 0],
                  color: std !== null && std !== undefined && r.unitKwh > std ? C.red : C.green,
                },
              };
        }),
        label: {
          show: true,
          position: "right",
          distance: 8,
          color: C.slate,
          fontSize: 12,
          fontFamily: FONT,
          formatter: "{c}",
        },
        markLine: target === null
          ? undefined
          : {
              silent: true,
              symbol: "none",
              lineStyle: { color: C.blue, type: [4, 4], width: 1.4 },
              data: [{ xAxis: target }],
              label: {
                formatter: `车间目标 ${target}`,
                color: C.blue,
                fontSize: 11,
                position: "end",
              },
            },
      },
    ],
  };
}

/** 单指标柱状图（用于水耗 / 汽耗等） */
export function simpleBarOption(
  labels: string[],
  values: number[],
  opts: {
    name: string;
    unit?: string;
    color?: string;
    labelInterval?: number | "auto";
  } = { name: "数值" },
): EChartsOption {
  const unit = opts.unit ?? "";
  const color = opts.color ?? C.blue;

  return {
    animationDuration: 520,
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{
          axisValue: string;
          marker: string;
          seriesName: string;
          value: number;
        }>;
        return `<b style="color:${C.text}">${arr[0].axisValue}</b><br>${arr[0].marker}${arr[0].seriesName}：<b>${arr[0].value}</b> ${unit}`;
      },
    },
    grid: { left: 4, right: 4, top: 22, bottom: 2, containLabel: true },
    xAxis: catAxis(labels, opts.labelInterval ?? "auto"),
    yAxis: valAxis(""),
    series: [
      {
        name: opts.name,
        type: "bar",
        barMaxWidth: 22,
        data: values,
        itemStyle: {
          borderRadius: [2, 2, 0, 0],
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color },
              { offset: 1, color: `rgba(${hexToRgb(color)},.35)` },
            ],
          },
        },
      },
    ],
  };
}

function hexToRgb(hex: string): string {
  const h = hex.replace("#", "");
  return `${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)}`;
}

export const chartColors = C;

/* ══════════════════════════════════════════════════════════════
   v1.4.0 数据中心：按能源的累计趋势图 + 单箱变动费用主图
   ══════════════════════════════════════════════════════════════ */

/**
 * 三条序列的单位天然不同（kWh / 瓶 / kWh每千瓶），**绝不能塞进同一根轴**：
 * 共轴会让小的那条被压成贴地直线，读图的人就以为「这条线没用过多少」。
 * 所以用三根轴：左＝累计用量，右＝累计产量，再往右偏移一根＝累计单耗。
 * 某条整段为空（产量未接入时的常态）就把那根轴**整条隐藏**，
 * 而不是留一根 0—1 的空轴让人以为图坏了。
 */
export interface EnergyCumulativeInput {
  labels: string[];
  usage: (number | null)[];
  usageUnit: string;
  output: (number | null)[];
  unitUse: (number | null)[];
  /** 单耗轴标题，如 'kWh/千瓶' 或 't/千瓶' */
  unitUseLabel: string;
  color: string;
  labelInterval?: number | "auto";
}

const hasValue = (arr: (number | null)[]) =>
  arr.some((v) => v !== null && v !== undefined);

export function energyCumulativeOption(
  i: EnergyCumulativeInput,
): EChartsOption {
  const showOutput = hasValue(i.output);
  const showUnit = hasValue(i.unitUse);
  // 单耗轴在产量未接入时全空 → 只画累计用量一条，图例也要跟着少一项
  const series: Record<string, unknown>[] = [
    {
      name: `累计用量（${i.usageUnit}）`,
      type: "line",
      yAxisIndex: 0,
      data: i.usage,
      connectNulls: false,
      smooth: true,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { width: 2.2, color: i.color },
      itemStyle: { color: i.color, borderColor: "#ffffff", borderWidth: 1.5 },
      areaStyle: { color: i.color, opacity: 0.12 },
    },
  ];
  if (showOutput) {
    series.push({
      name: "累计产量（瓶）",
      type: "bar",
      yAxisIndex: 1,
      data: i.output,
      barMaxWidth: 18,
      itemStyle: { color: "rgba(134,144,156,.45)", borderRadius: [2, 2, 0, 0] },
    });
  }
  if (showUnit) {
    series.push({
      name: `累计单耗（${i.unitUseLabel}）`,
      type: "line",
      yAxisIndex: 2,
      data: i.unitUse,
      connectNulls: false,
      smooth: true,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { width: 1.8, color: C.amber, type: [6, 4] },
      itemStyle: { color: C.amber, borderColor: "#ffffff", borderWidth: 1.5 },
    });
  }

  const yAxis: Record<string, unknown>[] = [
    { ...valAxis(i.usageUnit), position: "left" },
    {
      ...valAxis(""),
      position: "right",
      show: showOutput,
      splitLine: { show: false },
    },
    {
      ...valAxis(i.unitUseLabel),
      position: "right",
      offset: showOutput ? 56 : 0,
      show: showUnit,
      splitLine: { show: false },
    },
  ];

  return {
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{
          marker: string;
          seriesName: string;
          value: number | null;
          axisValue: string;
        }>;
        let s = `<b style="color:${C.text}">${arr[0].axisValue}</b><br>`;
        arr.forEach((p) => {
          s += `${p.marker}${p.seriesName}：<b>${p.value ?? "--"}</b><br>`;
        });
        return s;
      },
    },
    legend: {
      show: true,
      right: 0,
      top: 0,
      itemWidth: 8,
      itemHeight: 8,
      icon: "roundRect",
      textStyle: { color: C.slate, fontSize: 12, fontFamily: FONT },
    },
    grid: {
      left: 4,
      right: showUnit && showOutput ? 116 : 48,
      top: 34,
      bottom: 2,
      containLabel: true,
    },
    xAxis: catAxis(i.labels, i.labelInterval ?? "auto"),
    yAxis,
    series,
  } as EChartsOption;
}

/**
 * 单箱变动费用主图：实际值折线 + 「每月标准变动费用」水平虚线。
 *
 * ⚠ 标准值未标定（null）时**不画那条线**。画成 0 会得到
 *   「实际费用远高于标准 0.00 元/箱」的假结论 —— 与本项目「缺数绝不补 0」同一条铁律。
 * ⚠ 算料不齐（缺单价 / 缺产量）时整段为空，界面必须把 `blockers` 显示出来，
 *   否则现场只看到一张空图，会怀疑系统坏了。
 */
export function costPerCaseOption(
  labels: string[],
  perCase: (number | null)[],
  standard: number | null,
  opts: { labelInterval?: number | "auto" } = {},
): EChartsOption {
  const series: Record<string, unknown>[] = [
    {
      name: "单箱变动费用（元/箱）",
      type: "line",
      data: perCase,
      connectNulls: false,
      smooth: true,
      symbol: "circle",
      symbolSize: 6,
      lineStyle: { width: 2.4, color: C.blue },
      itemStyle: { color: C.blue, borderColor: "#ffffff", borderWidth: 1.5 },
      areaStyle: { color: C.blue, opacity: 0.1 },
    },
  ];
  if (standard !== null && standard > 0) {
    series[0].markLine = {
      silent: true,
      symbol: "none",
      lineStyle: { color: C.red, type: [6, 4], width: 1.6 },
      data: [{ yAxis: standard }],
      label: {
        formatter: `标准 ${standard} 元/箱`,
        color: C.red,
        fontSize: 11,
        position: "insideEndTop",
        fontFamily: FONT,
      },
    };
  }

  return {
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{
          marker: string;
          seriesName: string;
          value: number | null;
          axisValue: string;
        }>;
        const v = arr[0]?.value;
        return (
          `<b style="color:${C.text}">${arr[0]?.axisValue ?? ""}</b><br>` +
          `${arr[0]?.marker ?? ""}单箱变动费用：<b>${v ?? "--"}</b> 元/箱` +
          (standard !== null
            ? `<br><span style="color:${C.red}">标准：${standard} 元/箱</span>`
            : "")
        );
      },
    },
    legend: {
      show: true,
      right: 0,
      top: 0,
      itemWidth: 8,
      itemHeight: 8,
      icon: "roundRect",
      textStyle: { color: C.slate, fontSize: 12, fontFamily: FONT },
    },
    grid: { left: 4, right: 12, top: 34, bottom: 2, containLabel: true },
    xAxis: catAxis(labels, opts.labelInterval ?? "auto"),
    yAxis: [{ ...valAxis("元/箱"), min: 0 }],
    series,
  } as EChartsOption;
}

/**
 * 区间能源趋势图（用量查询页用）：电 / 水 / 汽 各一条，用户勾选哪几条画哪几条。
 *
 * ⚠ 三种能源单位不同（kWh 与 t 差着数量级），**共轴会把水/汽压成贴地直线**，
 *   所以每种能源一根轴、依次向右偏移；只勾一条时退回单轴。
 */
export interface EnergyUsageSeries {
  key: string;
  name: string;
  unit: string;
  color: string;
  data: (number | null)[];
  /** 该能源在当前口径下根本没有数据（如选单线时的水/汽） */
  emptyHint?: string;
}

export function energyUsageOption(
  labels: string[],
  series: EnergyUsageSeries[],
  opts: { labelInterval?: number | "auto" } = {},
): EChartsOption {
  const shown = series.filter((s) =>
    s.data.some((v) => v !== null && v !== undefined),
  );
  // 序列与轴**一一对应**（第 i 条序列用第 i 根轴）。
  // 之前写成 `i === 0 ? 0 : i + 1`，第二条序列指向了不存在的 yAxis，
  // ECharts 直接抛 `yAxis "0" not found` —— 而且它是在 ChartPanel 的 mounted 里抛的，
  // 会中断整棵子树的挂载，症状却是「下面表格的列全没了」，完全看不出关联。

  return {
    tooltip: {
      ...tip(),
      formatter: (ps: unknown) => {
        const arr = ps as Array<{
          marker: string;
          seriesName: string;
          value: number | null;
          axisValue: string;
        }>;
        let s = `<b style="color:${C.text}">${arr[0]?.axisValue ?? ""}</b><br>`;
        arr.forEach((p) => {
          s += `${p.marker}${p.seriesName}：<b>${p.value ?? "--"}</b><br>`;
        });
        return s;
      },
    },
    legend: {
      show: true,
      right: 0,
      top: 0,
      itemWidth: 8,
      itemHeight: 8,
      icon: "roundRect",
      textStyle: { color: C.slate, fontSize: 12, fontFamily: FONT },
    },
    // 每多一根右轴多留 56px
    grid: {
      left: 4,
      right: 12 + Math.max(0, shown.length - 1) * 56,
      top: 34,
      bottom: 2,
      containLabel: true,
    },
    xAxis: catAxis(labels, opts.labelInterval ?? "auto"),
    yAxis: [
      { ...valAxis(shown[0]?.unit ?? ""), position: "left", min: 0 },
      ...shown.slice(1).map((s, i) => ({
        ...valAxis(s.unit),
        position: "right" as const,
        offset: (i + 1) * 56,
        min: 0,
        splitLine: { show: false },
      })),
    ],
    series: shown.map((s, i) => ({
      name: `${s.name}（${s.unit}）`,
      type: "line" as const,
      yAxisIndex: i,
      data: s.data,
      connectNulls: false,
      smooth: true,
      symbol: "circle" as const,
      symbolSize: 5,
      lineStyle: { width: 2.2, color: s.color },
      itemStyle: { color: s.color, borderColor: "#ffffff", borderWidth: 1.5 },
      areaStyle: { color: s.color, opacity: 0.1 },
    })),
  } as EChartsOption;
}
