<script setup lang="ts">
/**
 * ChartBox —— ECharts 容器
 * 传入 option 即自动挂载 / 更新 / 销毁，并跟随舞台缩放自动 resize。
 *
 * ⚠ option 的首次赋值可能发生在组件挂载**之前**（父组件用 watch(immediate) 喂数据），
 *   这正是 v1.2.1 之前「第一次进页面图表空白」的原因。
 *   现在由 useECharts 内部缓存并在挂载后补渲染，这里不需要额外处理，
 *   但**不要**把 immediate 去掉 —— 去掉会让父组件在挂载前算出的一次性 option 丢失。
 */
import { ref } from 'vue';
import type { EChartsOption } from 'echarts';
import { useECharts } from '@/composables/useEcharts';

const props = defineProps<{
  option: EChartsOption | null;
  /** 额外容器类，如 chart--grow */
  grow?: boolean;
}>();

const el = ref<HTMLElement | null>(null);
const { setOption } = useECharts(el);

/**
 * 显式 watch（不用 watchEffect）：option 是 computed，父组件重新计算时
 * 引用才会变化；deep: false 避免 ECharts 大对象被逐层遍历。
 */
import { watch } from 'vue';
watch(
  () => props.option,
  (opt) => {
    if (opt) setOption(opt);
  },
  { immediate: true, deep: false }
);
</script>

<template>
  <div ref="el" class="chart" :class="{ 'chart--grow': grow }" />
</template>
