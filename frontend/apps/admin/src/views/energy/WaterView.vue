<script setup lang="ts">
/**
 * WaterView —— 水计量（车间进水总表）
 * 页面逻辑与汽计量完全一致，共用 UtilityUsageView，见该文件的口径说明。
 */
import UtilityUsageView from './UtilityUsageView.vue';
</script>

<template>
  <UtilityUsageView energy="water" />
</template>
