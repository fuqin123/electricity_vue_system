<script setup lang="ts">
/**
 * StatCard —— 指标卡
 * 左侧 3px 主色描边 + 大号等宽数字 + 底部趋势说明，用于仪表盘与各统计页。
 */
import { hexA } from '@eep/shared';

const props = withDefaults(
  defineProps<{
    label: string;
    value: string | number;
    unit?: string;
    /** 强调色，作用于左侧描边与数值下方的说明 */
    color?: string;
    /** 底部说明文本 */
    foot?: string;
    /** 趋势方向：up 上升 / down 下降 / flat 持平，仅控制说明文字颜色 */
    trend?: 'up' | 'down' | 'flat';
    /** 数值加载中 */
    loading?: boolean;
  }>(),
  { color: '#1664ff', trend: 'flat' }
);

const trendColor = () => {
  if (props.trend === 'flat') return 'var(--yl-text-subtle)';
  // 能源场景为「多用即变差」，故上升一律标红、下降标绿，非股票涨跌语义
  return props.trend === 'up' ? '#d7312a' : '#2a814b';
};

const accent = () => hexA(props.color, 0.12);
</script>

<template>
  <div class="stat" :style="{ background: `linear-gradient(90deg, ${accent()} 0%, #ffffff 46%)` }">
    <span class="stat__accent" :style="{ background: color }" />
    <div class="stat__label">{{ label }}</div>
    <div class="stat__value">
      <template v-if="loading"><el-skeleton-item variant="text" style="width: 90px; height: 26px" /></template>
      <template v-else>
        {{ value }}<span v-if="unit" class="stat__unit">{{ unit }}</span>
      </template>
    </div>
    <div v-if="foot" class="stat__foot" :style="{ color: trendColor() }">{{ foot }}</div>
  </div>
</template>
