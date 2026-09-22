<script setup lang="ts">
/**
 * PageHeader —— 页面头（标题 / 描述 / 操作区）+ 面包屑
 * 所有列表页、详情页、配置页共用，保持页首留白与信息层级一致。
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { breadcrumbOf } from '@/router/menu';

const props = defineProps<{
  title?: string;
  desc?: string;
  /** 是否显示面包屑（默认显示） */
  breadcrumb?: boolean;
}>();

const route = useRoute();

const title = computed(() => props.title ?? (route.meta?.title as string) ?? '');
const desc = computed(() => props.desc ?? (route.meta?.desc as string) ?? '');
const crumbs = computed(() => breadcrumbOf(route.path));
</script>

<template>
  <div class="page-head-wrap">
    <el-breadcrumb v-if="breadcrumb !== false && crumbs.length" separator="/" class="page-crumb">
      <el-breadcrumb-item v-for="c in crumbs" :key="c">{{ c }}</el-breadcrumb-item>
    </el-breadcrumb>

    <div class="page-head">
      <div class="page-head__left">
        <h1 class="page-head__title">{{ title }}</h1>
        <p v-if="desc" class="page-head__desc">{{ desc }}</p>
      </div>
      <div class="page-head__actions">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.page-head-wrap { margin-bottom: 16px; }

.page-crumb {
  margin-bottom: 8px;
  font-size: 12px;

  :deep(.el-breadcrumb__inner) { color: $yl-text-subtle; font-weight: 400; }
  :deep(.el-breadcrumb__item:last-child .el-breadcrumb__inner) { color: #737a87; }
}
</style>
