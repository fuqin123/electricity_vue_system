<script setup lang="ts">
/**
 * SideNav —— 侧边导航（200px，源力设计系统基准）
 * 菜单数据源：router/menu.ts，与路由一一对应；选中态为浅蓝底 + 主色文字。
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { useAppStore } from '@/stores/app';
import { menuTree } from '@/router/menu';
import { resolveIcon } from '@/utils/icons';

const route = useRoute();
const app = useAppStore();

const activePath = computed(() => route.path);

/** 展开当前分组，避免刷新子路由时父级折叠 */
const defaultOpeneds = computed(() =>
  menuTree.filter((m) => m.children?.some((c) => c.path === route.path)).map((m) => m.path)
);
</script>

<template>
  <aside class="sidenav" :class="{ 'is-collapsed': app.sidebarCollapsed }">
    <el-menu
      class="sidenav__menu"
      :default-active="activePath"
      :default-openeds="defaultOpeneds"
      :collapse="app.sidebarCollapsed"
      :collapse-transition="false"
      unique-opened
      router
    >
      <template v-for="node in menuTree" :key="node.path">
        <!-- 目录节点 -->
        <el-sub-menu v-if="node.children?.length" :index="node.path">
          <template #title>
            <el-icon v-if="resolveIcon(node.icon)"><component :is="resolveIcon(node.icon)" /></el-icon>
            <span>{{ node.title }}</span>
          </template>
          <el-menu-item v-for="child in node.children" :key="child.path" :index="child.path">
            <el-icon v-if="resolveIcon(child.icon ?? '')"><component :is="resolveIcon(child.icon ?? '')" /></el-icon>
            <template #title>{{ child.title }}</template>
          </el-menu-item>
        </el-sub-menu>

        <!-- 单页节点 -->
        <el-menu-item v-else :index="node.path">
          <el-icon v-if="resolveIcon(node.icon)"><component :is="resolveIcon(node.icon)" /></el-icon>
          <template #title>{{ node.title }}</template>
        </el-menu-item>
      </template>
    </el-menu>
  </aside>
</template>

<style scoped lang="scss">
.sidenav {
  width: $yl-sidebar-w;
  min-width: $yl-sidebar-w;
  flex: 0 0 $yl-sidebar-w;
  background: $yl-bg-sidebar;
  border-right: 1px solid $yl-border;
  overflow-x: hidden;
  overflow-y: auto;
  transition: width 0.16s ease, min-width 0.16s ease, flex-basis 0.16s ease;

  &.is-collapsed {
    width: 64px;
    min-width: 64px;
    flex-basis: 64px;
  }
}

.sidenav__menu {
  border-right: none;
  background: transparent;
  padding: 12px 8px 24px;

  &:not(.el-menu--collapse) { width: 100%; }
}

/* 菜单项：36px 高、4px 圆角、13px 字号（源力侧栏规格） */
.sidenav__menu :deep(.el-menu-item),
.sidenav__menu :deep(.el-sub-menu__title) {
  height: 36px;
  line-height: 36px;
  font-size: 13px;
  color: #1d2129;
  border-radius: 4px;
  margin-bottom: 4px;
}

.sidenav__menu :deep(.el-menu-item:hover),
.sidenav__menu :deep(.el-sub-menu__title:hover) {
  background: rgba(0, 0, 0, 0.04);
}

.sidenav__menu :deep(.el-menu-item.is-active) {
  background: rgba(22, 100, 255, 0.06) !important;
  color: $yl-primary;
  font-weight: 500;
}

.sidenav__menu :deep(.el-sub-menu .el-menu-item) {
  min-width: auto;
  padding-left: 40px !important;
}

.sidenav__menu :deep(.el-icon) {
  font-size: 16px;
  margin-right: 8px;
}
</style>
