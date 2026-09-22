<script setup lang="ts">
/**
 * BrandMark —— 本系统自有品牌标识（源力蓝几何标）
 *
 * 说明：源力设计系统自带的 nav-logo 属于设计系统提供方的品牌资产，
 * 本项目为厂内自用系统，故按设计系统的尺寸与色彩规范（24px 高、主色 #1664FF）
 * 绘制自有标识，不复用第三方商标。
 */
defineProps<{ size?: number }>();
</script>

<template>
  <svg
    :height="size ?? 24"
    :width="(size ?? 24) * 1.35"
    viewBox="0 0 32 24"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-label="能源产效管理系统"
  >
    <!-- 外框：象征车间 / 厂房轮廓 -->
    <rect x="1" y="5" width="14" height="18" rx="2" stroke="#1664FF" stroke-width="1.6" />
    <!-- 闪电：能源 -->
    <path d="M8.6 8.4L5.2 14.6h3.2l-1 4.4 4.2-6.6H7.9l0.7-4z" fill="#1664FF" />
    <!-- 上扬折线：产效提升 -->
    <path
      d="M18.5 19.5l3.6-5.2 3.2 2.8 4.6-8"
      stroke="#37E2E2"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <circle cx="29.9" cy="9.1" r="2" fill="#37E2E2" />
  </svg>
</template>
