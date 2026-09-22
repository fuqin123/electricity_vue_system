import { computed, ref, shallowRef } from 'vue';
import { defineStore } from 'pinia';
import { dataConfig, energyApi, type EnergySnapshot, type LineData, type TrendDim } from '@eep/shared';

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

  let timer: number | null = null;

  const ready = computed(() => snapshot.value !== null);
  const workshop = computed(() => snapshot.value?.workshop ?? null);
  const lines = computed<LineData[]>(() => snapshot.value?.workshop.lines ?? []);
  const currentLine = computed<LineData | null>(
    () => lines.value.find((l) => l.id === currentLineId.value) ?? lines.value[0] ?? null
  );

  async function refresh(): Promise<void> {
    loading.value = true;
    try {
      snapshot.value = await energyApi.getSnapshot(new Date());
      refreshedAt.value = new Date();
      errorMsg.value = '';
    } catch (e) {
      errorMsg.value = (e as Error).message;
      // 保持上一帧画面，避免大屏在黑屏与正常之间闪跳
      console.error('[数据获取失败]', e);
    } finally {
      loading.value = false;
    }
  }

  function start(): void {
    void refresh();
    if (timer !== null) window.clearInterval(timer);
    // 刷新间隔下限保护，避免配置误填导致接口被打爆
    timer = window.setInterval(() => void refresh(), Math.max(dataConfig.refreshMs, 5000));
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
    refresh,
    start,
    stop,
    setDim,
    setLine
  };
});
