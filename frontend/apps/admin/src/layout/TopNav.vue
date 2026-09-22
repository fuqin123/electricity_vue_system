<script setup lang="ts">
/**
 * TopNav —— 顶栏（48px，源力设计系统基准）
 * 结构：折叠按钮 + 品牌 + 右侧工具区（搜索 / 大屏入口 / 通知 / 用户）
 */
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { plantConfig } from '@eep/shared';
import { useAppStore } from '@/stores/app';
import { useAuthStore } from '@/stores/auth';
import BrandMark from './BrandMark.vue';

const app = useAppStore();
const auth = useAuthStore();
const router = useRouter();

const keyword = ref('');

function onSearch() {
  if (!keyword.value.trim()) return;
  ElMessage.info(`全局搜索「${keyword.value.trim()}」：接口接入后跳转检索结果页`);
}

/** 跳转到数据大屏（独立应用，同域下部署在 /screen/） */
function openScreen() {
  window.open('/screen/', '_blank', 'noopener');
}

async function onCommand(cmd: string) {
  if (cmd === 'logout') {
    try {
      await ElMessageBox.confirm('确认退出登录？', '提示', { type: 'warning' });
    } catch {
      return;
    }
    await auth.logout();
    void router.replace({ name: 'login' });
  } else if (cmd === 'profile') {
    ElMessage.info('个人中心：本期为框架阶段，页面待接入');
  }
}
</script>

<template>
  <header class="top-nav">
    <button class="hamburger" type="button" :title="app.sidebarCollapsed ? '展开菜单' : '收起菜单'" @click="app.toggleSidebar()">
      <span class="hamburger__line" />
      <span class="hamburger__line" />
      <span class="hamburger__line" />
    </button>

    <div class="brand">
      <BrandMark :size="24" />
      <span class="brand__name">{{ plantConfig.shortName }}</span>
      <span class="brand__divider" />
      <span class="brand__sub">{{ plantConfig.name }}</span>
    </div>

    <div class="nav-right">
      <div class="search-box">
        <el-icon class="search-box__icon"><Search /></el-icon>
        <input v-model="keyword" class="search-box__input" placeholder="搜索产线 / 点位 / 告警编号" @keyup.enter="onSearch" />
      </div>

      <a class="nav-link" href="#" @click.prevent="openScreen">
        <el-icon><Monitor /></el-icon>
        数据大屏
      </a>

      <div class="nav-icon" title="通知">
        <el-icon><Bell /></el-icon>
        <span class="notification-dot" />
      </div>

      <el-dropdown trigger="click" @command="onCommand">
        <div class="user">
          <span class="avatar">{{ auth.user?.avatarText ?? 'U' }}</span>
          <span class="user__meta">
            <b>{{ auth.displayName }}</b>
            <em>{{ auth.user?.roleName ?? '—' }}</em>
          </span>
          <el-icon class="user__caret"><ArrowDown /></el-icon>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="profile">个人中心</el-dropdown-item>
            <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<style scoped lang="scss">
.top-nav {
  height: $yl-topnav-h;
  flex: 0 0 $yl-topnav-h;
  display: flex;
  align-items: center;
  background: $yl-bg;
  box-shadow: 0 1px 0 0 $yl-border, 0 1px 4px 0 rgba(12, 13, 14, 0.04);
  position: relative;
  z-index: 100;
}

.hamburger {
  width: $yl-topnav-h;
  height: $yl-topnav-h;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  cursor: pointer;
  flex: 0 0 $yl-topnav-h;
  border: none;
  background: transparent;
  padding: 0;

  &:hover { background: $yl-bg-hover; }
}

.hamburger__line {
  width: 16px;
  height: 2px;
  background: #42464e;
  border-radius: 1px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 4px;
  min-width: 0;
}

.brand__name {
  font-size: 16px;
  font-weight: 500;
  color: #0c0d0e;
  white-space: nowrap;
}

.brand__divider {
  width: 1px;
  height: 14px;
  background: $yl-border-strong;
  margin: 0 2px;
}

.brand__sub {
  font-size: 13px;
  color: $yl-text-subtle;
  white-space: nowrap;
}

.nav-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 16px;
  padding-right: 20px;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 6px;
  height: $yl-control-h;
  padding: 0 12px;
  border: 1px solid $yl-border;
  border-radius: $yl-radius;
  background: $yl-bg-sidebar;

  &:focus-within {
    border-color: $yl-primary;
    background: $yl-bg;
  }
}

.search-box__icon { color: $yl-text-subtle; font-size: 14px; }

.search-box__input {
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  color: #0c0d0e;
  width: 180px;
  font-family: inherit;

  &::placeholder { color: $yl-text-subtle; }
}

.nav-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  color: #1d2129;
  text-decoration: none;
  white-space: nowrap;

  &:hover { color: $yl-primary; }
}

.nav-icon {
  position: relative;
  display: flex;
  align-items: center;
  font-size: 16px;
  color: #42464e;
  cursor: pointer;

  &:hover { color: $yl-primary; }
}

.notification-dot {
  position: absolute;
  top: -2px;
  right: -2px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: $yl-danger;
}

.user {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  outline: none;
  padding: 4px 6px;
  border-radius: $yl-radius;

  &:hover { background: $yl-bg-hover; }
}

.avatar {
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  border-radius: 50%;
  background: #ebf1ff;
  color: $yl-primary;
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}

.user__meta {
  display: flex;
  flex-direction: column;
  line-height: 1.25;

  b { font-size: 13px; font-weight: 500; color: #0c0d0e; }
  em { font-style: normal; font-size: 11px; color: $yl-text-subtle; }
}

.user__caret { font-size: 12px; color: $yl-text-subtle; }
</style>
