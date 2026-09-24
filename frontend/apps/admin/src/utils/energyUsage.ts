/**
 * useEnergyUsage —— 电 / 水 / 汽三个计量页共用的「按区间查询」逻辑
 *
 * 为什么抽出来：这三页的筛选、取数、明细行、图表是同一件事，只差能源类型与单位。
 * 原来各写一份，结果三页的明细表**全都恒空**（B-519：TablePage 在快照返回前就取完数，
 * 之后没有任何机制再取一次），修一处要改三处。
 *
 * 口径全部走 `/api/energy/range` —— 与后台「用量查询」页、大屏同一份实现，
 * 三处数字必然一致（同一指标只能有一个来源）。
 */
import { computed, onMounted, ref, type Ref } from 'vue';
import { ElMessage } from 'element-plus';
import { energyApi, type UsageRangeResult } from '@eep/shared';
import { energyUsageOption, type EnergyUsageSeries } from './charts';

export type UsageEnergy = 'electric' | 'water' | 'steam';
export type UsageGranularity = 'hour' | 'day' | 'month';

export interface UsageRow {
  id: number;
  label: string;
  usageText: string;
  deltaText: string;
  deltaColor: string;
  quality: string;
}

const META: Record<UsageEnergy, { name: string; unit: string; color: string; perLine: boolean; emptyHint: string }> = {
  electric: { name: '用电', unit: 'kWh', color: '#1664ff', perLine: true, emptyHint: '本区间电表无可信差值' },
  water: { name: '用水', unit: 't', color: '#387bff', perLine: false, emptyHint: '车间进水总表未接入' },
  steam: { name: '用汽', unit: 't', color: '#bd7e00', perLine: false, emptyHint: '蒸汽总表本区间无数据' }
};

/** 本地时间字符串。⚠ 不能用 toISOString（UTC，东八区早上会得到昨天） */
function fmt(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function useEnergyUsage(energy: UsageEnergy) {
  const meta = META[energy];

  const range = ref<[string, string]>([fmt(new Date(new Date().setHours(0, 0, 0, 0))), fmt(new Date())]);
  const granularity = ref<UsageGranularity>('hour');
  /** 空串 = 车间合计；否则为产线编码（只有电支持按线） */
  const lineCode = ref('');
  const loading = ref(false);
  const data = ref<UsageRangeResult | null>(null);

  const scope = computed<'workshop' | 'line'>(() => (meta.perLine && lineCode.value ? 'line' : 'workshop'));

  async function load() {
    if (!range.value?.[0] || !range.value?.[1]) {
      ElMessage.warning('请选择起止时间');
      return;
    }
    loading.value = true;
    try {
      data.value = await energyApi.getRange({
        from: range.value[0],
        to: range.value[1],
        granularity: granularity.value,
        scope: scope.value,
        line: scope.value === 'line' ? lineCode.value : undefined
      });
    } catch (e) {
      ElMessage.error((e as Error).message);
      data.value = null;
    } finally {
      loading.value = false;
    }
  }

  onMounted(load);

  /** 本能源的逐桶用量 */
  const values = computed<(number | null)[]>(() => {
    const d = data.value;
    if (!d) return [];
    if (energy === 'electric') {
      const wantKey = d.line ?? 'workshop';
      const s = d.series.find((x) => x.key === wantKey) ?? d.series[0];
      return s?.values ?? [];
    }
    return energy === 'water' ? d.utility.water : d.utility.steam;
  });

  /**
   * 明细行：时间 / 用量 / 较上一时段 / 数据质量。
   * 缺数一律 `--`，且**不参与环比** —— 拿缺数格当 0 去比会凭空造出一个 -100%。
   */
  const rows = computed<UsageRow[]>(() => {
    const d = data.value;
    if (!d) return [];
    const list: UsageRow[] = values.value.map((v, i) => {
      const prev = i > 0 ? values.value[i - 1] : null;
      const delta = v !== null && prev !== null && prev > 0 ? ((v - prev) / prev) * 100 : null;
      return {
        id: i,
        label: d.labels[i],
        usageText: v === null || v === undefined ? '--' : String(v),
        deltaText: delta === null ? '--' : `${delta > 0 ? '+' : ''}${delta.toFixed(1)}%`,
        deltaColor: delta === null ? '#86909c' : delta > 0 ? '#d7312a' : '#2a814b',
        quality: d.quality?.[i] ?? ''
      };
    });
    return list.reverse(); // 最新在上
  });

  const totals = computed(() => {
    const d = data.value;
    if (!d) return null;
    return {
      kwh: d.totals.kwh,
      waterT: d.totals.waterT,
      steamT: d.totals.steamT,
      buckets: d.bucketCount,
      from: (d as UsageRangeResult & { fromHour?: string }).fromHour ?? d.from,
      to: (d as UsageRangeResult & { toHour?: string }).toHour ?? d.to
    };
  });

  const missingBuckets = computed(() => values.value.filter((v) => v === null || v === undefined).length);

  /** 有值但整段为空时给一句话说明，避免「图是空的」被读成「系统坏了」 */
  const emptyHint = computed(() => (values.value.length && !missingBuckets.value ? '' : values.value.length === 0 || missingBuckets.value === values.value.length ? meta.emptyHint : ''));

  const chartOption = computed(() => {
    const d = data.value;
    if (!d) return null;
    const series: EnergyUsageSeries[] = [
      { key: energy, name: `车间${meta.name}`, unit: meta.unit, color: meta.color, data: values.value, emptyHint: meta.emptyHint }
    ];
    return energyUsageOption(d.labels, series, { labelInterval: d.labels.length > 24 ? 2 : 'auto' });
  });

  /**
   * 传给 TablePage 的 extraQuery：
   * ⚠ 这里必须带上「数据是否已就绪」—— TablePage 只在挂载与 extraQuery 变化时取数，
   *   不带它就回到 B-519 的老问题（快照还没回来就取完数，之后再也不刷新）。
   */
  const extraQuery: Ref<Record<string, unknown>> = computed(() => ({
    ready: data.value !== null,
    buckets: data.value?.bucketCount ?? 0,
    energy
  }));

  return {
    meta,
    range,
    granularity,
    lineCode,
    loading,
    data,
    scope,
    load,
    rows,
    values,
    totals,
    missingBuckets,
    emptyHint,
    chartOption,
    extraQuery
  };
}

/** 快捷区间：三页共用，避免每页各写一份 */
export function usageQuickSpans(): { label: string; hours: number; gran: UsageGranularity }[] {
  return [
    { label: '近 6 小时', hours: 6, gran: 'hour' },
    { label: '今日', hours: 0, gran: 'hour' },
    { label: '近 3 日', hours: 72, gran: 'hour' },
    { label: '近 7 日', hours: 168, gran: 'day' },
    { label: '近 30 日', hours: 720, gran: 'day' }
  ];
}

export function hourAgo(hours: number): string {
  const d = new Date();
  d.setHours(d.getHours() - hours);
  if (hours === 0) return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 00:00`;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function nowHourText(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:00`;
}
