<script setup lang="ts">
/**
 * ChartPanel —— 图表卡片
 * 统一容器：标题 + 右上角操作区 + ECharts 画布，跟随容器尺寸自适应。
 */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import * as echarts from 'echarts';
import type { EChartsOption } from 'echarts';

const props = withDefaults(
  defineProps<{
    title: string;
    option: EChartsOption | null;
    /** 画布高度（px），默认 280 */
    height?: number;
    /** 卡片右上角说明文字 */
    note?: string;
  }>(),
  { height: 280 }
);

const el = ref<HTMLElement | null>(null);
const chart = shallowRef<echarts.ECharts | null>(null);
let observer: ResizeObserver | null = null;

function render() {
  if (!el.value) return;
  if (!chart.value) chart.value = echarts.init(el.value);
  if (props.option) chart.value.setOption(props.option, true);
}

function resize() {
  chart.value?.resize();
}

watch(() => props.option, render);

onMounted(() => {
  render();
  if (el.value && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => resize());
    observer.observe(el.value);
  }
  window.addEventListener('resize', resize);
});

onBeforeUnmount(() => {
  observer?.disconnect();
  window.removeEventListener('resize', resize);
  chart.value?.dispose();
  chart.value = null;
});
</script>

<template>
  <div class="card">
    <div class="card__head">
      <h3 class="card__title">{{ title }}</h3>
      <div class="card__extra">
        <slot name="extra"><span v-if="note">{{ note }}</span></slot>
      </div>
    </div>
    <div ref="el" class="chart-box" :style="{ height: height + 'px' }" />
  </div>
</template>
