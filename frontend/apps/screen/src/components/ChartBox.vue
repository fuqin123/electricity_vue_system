<script setup lang="ts">
/**
 * ChartBox —— ECharts 容器
 * 传入 option 即自动挂载 / 更新 / 销毁，并跟随舞台缩放自动 resize。
 */
import { ref, watch } from 'vue';
import type { EChartsOption } from 'echarts';
import { useECharts } from '@/composables/useEcharts';

const props = defineProps<{
  option: EChartsOption | null;
  /** 额外容器类，如 chart--grow */
  grow?: boolean;
}>();

const el = ref<HTMLElement | null>(null);
const { setOption } = useECharts(el);

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
