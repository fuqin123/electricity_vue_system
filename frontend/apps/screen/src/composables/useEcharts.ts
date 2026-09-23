import { nextTick, onBeforeUnmount, onMounted, shallowRef, watch, type Ref } from 'vue';
import * as echarts from 'echarts';

type EChartsOption = echarts.EChartsOption;

/** 已创建实例的注册表，供全局 resize 使用 */
const registry = new Set<echarts.ECharts>();

/**
 * ECharts 实例生命周期封装
 *
 * ⚠ 这里有一个必须防住的时序坑（v1.2.1 修复，见 docs/02 的 B-401）：
 *   父组件常用 `watch(() => props.option, fn, { immediate: true })` 来喂数据。
 *   `immediate` 的回调在 **setup 阶段**就执行，此时组件还没挂载、模板 ref 仍是 null，
 *   `echarts.init()` 无从下手，option 就被**静默丢掉**了；只有等 option 下次变化
 *   （比如切换产线）才会补上 —— 表现就是「第一次进来图表空白，切走再切回来才有」。
 *
 *   因此这里改为两段式：**挂载前把 option 记在 pending 里，挂载后立刻补渲染**。
 *   任何调用方都不需要再关心自己是不是在 onMounted 里调用 setOption。
 *
 * 另外：容器尺寸变化（舞台缩放 / 窗口 resize / 侧栏折叠）自动 resize；
 * 组件卸载时销毁实例，避免大屏长时间挂机导致内存泄漏。
 */
export function useECharts(elRef: Ref<HTMLElement | null>) {
  const chart = shallowRef<echarts.ECharts | null>(null);

  /** 挂载前收到的 option（挂载后补渲染） */
  let pending: EChartsOption | null = null;
  /** 是否已完成 onMounted —— 决定 setOption 是直接渲染还是先记账 */
  let mounted = false;

  function init(): boolean {
    if (!elRef.value) return false;
    if (chart.value) return true;
    chart.value = echarts.init(elRef.value, undefined, { renderer: 'canvas' });
    registry.add(chart.value);
    return true;
  }

  /** 真正落地一次 setOption（已确保挂载） */
  function render(option: EChartsOption) {
    if (!chart.value && !init()) return;
    chart.value?.setOption(option, true);
  }

  function setOption(option: EChartsOption) {
    if (!mounted) {
      // 还没挂载：记账，等 onMounted 里补渲染，**不能丢**
      pending = option;
      return;
    }
    render(option);
  }

  function resize() {
    chart.value?.resize();
  }

  let observer: ResizeObserver | null = null;

  onMounted(async () => {
    mounted = true;
    init();

    if (pending) {
      render(pending);
      pending = null;
    }

    if (elRef.value && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => resize());
      observer.observe(elRef.value);
    }
    window.addEventListener('eep:stage-resize', resize);

    // 等一次 DOM 更新 + 一帧绘制：容器在挂载那一刻可能还是 0 尺寸
    // （父级 flex / grid 尚未完成布局），这里补一次尺寸同步。
    await nextTick();
    requestAnimationFrame(() => resize());
  });

  onBeforeUnmount(() => {
    mounted = false;
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
export function useViewport(ref: Ref<unknown> | (() => unknown), refresh: () => void) {
  onMounted(() => window.addEventListener('resize', refresh));
  onBeforeUnmount(() => window.removeEventListener('resize', refresh));
  watch(ref, refresh, { deep: false });
}
