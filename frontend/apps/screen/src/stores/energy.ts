import { computed, ref, shallowRef } from 'vue';
import { defineStore } from 'pinia';
import { dataConfig, energyApi, type EnergySnapshot, type LineData, type TrendDim } from '@eep/shared';

/**
 * 本地日期字符串 YYYY-MM-DD。
 * ⚠ 不能用 `toISOString().slice(0,10)` —— 那是 UTC，东八区在早上 8 点前会得到「昨天」。
 */
function isoToday(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * 大屏数据仓库
 *  · 定时拉取快照并广播给所有视图
 *  · 统一维护趋势维度（年/月/日/时）与当前产线，切屏不丢失状态
 */
export const useEnergyStore = defineStore('energy', () => {
  const snapshot = shallowRef<EnergySnapshot | null>(null);
  const loading = ref(false);
  const errorMsg = ref('');
  const dim = ref<TrendDim>('月');
  const currentLineId = ref('A1');
  const refreshedAt = ref<Date | null>(null);
  /** 当前查看的日期（YYYY-MM-DD）；等于今天就按实时处理 */
  const selectedDate = ref<string>(isoToday());

  let timer: number | null = null;
  /**
   * 请求序号 —— 用来丢弃**过期响应**（B-525）。
   *
   * `/api/energy/overview` 单次要跑 2—4 秒（六条线各自的小时格子都回查一遍聚合表）。
   * 连续切两次日期时，先发的请求可能后到，`snapshot.value` 就被旧的那一天覆盖：
   * 日期标签写着 09-27、表格里却是 09-25 的数字 —— 现场只会认定「大屏和后台对不上」
   * （实测反馈的「22 点一线 7.3」正是 09-25 同一格的值，后台当天同格是 7.8）。
   */
  let seq = 0;

  const ready = computed(() => snapshot.value !== null);
  /** 当前画面上的数据属于哪一天（与 `selectedDate` 不一致 = 还在显示上一天的数字）*/
  const snapshotDate = ref<string>('');
  /**
   * 画面与所选日期不一致（切换中）。
   * 一定要让界面说出来：单次快照要 2—4 秒，期间大屏显示的还是**上一天**的数字，
   * 而日期标签已经翻到新的一天 —— 现场按新日期去和后台核对，就会得到
   * 「同一个格子两个数」（B-525 的来由）。宁可显式标注，也不要静默地错位。
   */
  const stale = computed(() => snapshotDate.value !== selectedDate.value);
  /**
   * 是否在看历史数据。
   * 历史日期下**必须停掉自动刷新**：历史数据不会再变，
   * 每 30 秒重跑一遍只是白耗接口，还会让人误以为在看实时数据。
   */
  const isHistory = computed(() => selectedDate.value !== isoToday());
  /**
   * 数据来源：'api' 真实接口 / 'mock' 演示数据。
   * ⚠ 后端不可达时会降级为演示数据 —— 界面必须显示这个事实，
   *   否则现场会拿演示数字当真实产量做判断（比白屏危险得多）。
   */
  const source = computed<'api' | 'mock'>(() => snapshot.value?.source ?? 'mock');
  const notice = computed(() => snapshot.value?.notice ?? '');
  /** 是否为降级状态（后端不可达） */
  const degraded = computed(() => source.value === 'mock');
  const workshop = computed(() => snapshot.value?.workshop ?? null);
  const lines = computed<LineData[]>(() => snapshot.value?.workshop.lines ?? []);
  const currentLine = computed<LineData | null>(
    () => lines.value.find((l) => l.id === currentLineId.value) ?? lines.value[0] ?? null
  );

  async function refresh(): Promise<void> {
    const attempt = ++seq;
    const forDate = selectedDate.value;
    loading.value = true;
    try {
      const snap = await energyApi.getSnapshot({ date: forDate });
      // 已经有更新的请求在跑（或已经落过一帧更新的），这一帧必须丢掉：
      // 它是几天前的数据，盖上来就是「日期标签新的一格、数字旧的一天」。
      if (attempt !== seq) return;
      snapshot.value = snap;
      snapshotDate.value = forDate;
      refreshedAt.value = new Date();
      errorMsg.value = '';
    } catch (e) {
      if (attempt !== seq) return;
      errorMsg.value = (e as Error).message;
      // 保持上一帧画面，避免大屏在黑屏与正常之间闪跳
      console.error('[数据获取失败]', e);
    } finally {
      if (attempt === seq) loading.value = false;
    }
  }

  function start(): void {
    void refresh();
    if (timer !== null) window.clearInterval(timer);
    // 刷新间隔下限保护，避免配置误填导致接口被打爆。
    // 定时器常驻，但历史模式下直接跳过（见 isHistory 的说明）——
    // 比反复创建/销毁定时器更不容易出错。
    timer = window.setInterval(() => {
      if (isHistory.value) return;
      void refresh();
    }, Math.max(dataConfig.refreshMs, 5000));
  }

  function stop(): void {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  function setDim(d: TrendDim): void {
    dim.value = d;
  }

  function setLine(id: string): void {
    currentLineId.value = id.toUpperCase();
  }

  /** 切换查看日期；传空串或今天即回到实时 */
  function setDate(date: string): void {
    const next = date || isoToday();
    if (next === selectedDate.value) return;
    selectedDate.value = next;
    void refresh();
  }

  /** 回到今天（恢复实时刷新） */
  function backToToday(): void {
    setDate(isoToday());
  }

  return {
    snapshot,
    workshop,
    lines,
    currentLine,
    loading,
    errorMsg,
    dim,
    currentLineId,
    refreshedAt,
    ready,
    selectedDate,
    snapshotDate,
    stale,
    isHistory,
    source,
    notice,
    degraded,
    refresh,
    start,
    stop,
    setDim,
    setLine,
    setDate,
    backToToday
  };
});
