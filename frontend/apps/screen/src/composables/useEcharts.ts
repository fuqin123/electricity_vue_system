import { onBeforeUnmount, onMounted, ref, shallowRef, watch, type Ref } from 'vue';
import * as echarts from 'echarts';

type EChartsOption = echarts.EChartsOption;

/** 已创建实例的注册表，供全局 resize 使用 */
const registry = new Set<echarts.ECharts>();

/**
 * ECharts 实例生命周期封装
 *  · 容器尺寸变化（舞台缩放 / 窗口 resize / 侧栏折叠）自动 resize
 *  · 组件卸载时销毁实例，避免大屏长时间挂机导致内存泄漏
 */
export function useECharts(elRef: Ref<HTMLElement | null>) {
  const chart = shallowRef<echarts.ECharts | null>(null);

  function init() {
    if (!elRef.value) return;
    if (chart.value) return;
    chart.value = echarts.init(elRef.value, undefined, { renderer: 'canvas' });
    registry.add(chart.value);
  }

  function setOption(option: EChartsOption) {
    if (!chart.value) init();
    chart.value?.setOption(option, true);
  }

  function resize() {
    chart.value?.resize();
  }

  let observer: ResizeObserver | null = null;

  onMounted(() => {
    init();
    if (elRef.value && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => resize());
      observer.observe(elRef.value);
    }
    window.addEventListener('eep:stage-resize', resize);
  });

  onBeforeUnmount(() => {
    window.removeEventListener('eep:stage-resize', resize);
    observer?.disconnect();
    observer = null;
    if (chart.value) {
      registry.delete(chart.value);
      chart.value.dispose();
      chart.value = null;
    }
  });

  return { chart, setOption, resize, init };
}

/** 全局 resize：舞台缩放后统一刷新所有图表 */
export function resizeAllCharts() {
  registry.forEach((c) => {
    try {
      c.resize();
    } catch {
      /* 实例可能已销毁，忽略 */
    }
  });
}

/** 视口尺寸变化时监听（用于需要重算的布局） */
export function useViewport(refresh: () => void) {
  onMounted(() => window.addEventListener('resize', refresh));
  onBeforeUnmount(() => window.removeEventListener('resize', refresh));
  watch(ref, refresh, { deep: false });
}
