<script setup lang="ts">
/**
 * AlarmLevelTag —— 告警级别标签
 * 级别色取自源力设计系统状态色：严重=红、重要=橙、次要=黄、提示=蓝
 */
import { computed } from 'vue';

const props = defineProps<{ level: 'critical' | 'major' | 'minor' | 'info' | string }>();

const MAP: Record<string, { text: string; type: 'danger' | 'warning' | 'primary' | 'info'; color: string }> = {
  critical: { text: '严重', type: 'danger', color: '#d7312a' },
  major: { text: '重要', type: 'warning', color: '#bd7e00' },
  minor: { text: '次要', type: 'info', color: '#737a87' },
  info: { text: '提示', type: 'primary', color: '#1664ff' }
};

const meta = computed(() => MAP[props.level] ?? MAP.info);
</script>

<template>
  <el-tag :type="meta.type" size="small" disable-transitions>{{ meta.text }}</el-tag>
</template>
