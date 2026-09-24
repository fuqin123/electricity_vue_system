<script setup lang="ts">
/**
 * SteamView —— 汽计量（车间蒸汽总表，已温压补偿）
 * 页面逻辑与水计量完全一致，共用 UtilityUsageView，见该文件的口径说明。
 */
import UtilityUsageView from './UtilityUsageView.vue';
</script>

<template>
  <UtilityUsageView energy="steam" />
</template>
