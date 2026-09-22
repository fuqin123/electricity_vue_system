<script setup lang="ts">
/**
 * AdminLayout —— 后台整体骨架
 * 顶栏 48px + 侧边导航 200px + 内容区（含面包屑与页头）
 */
import TopNav from './TopNav.vue';
import SideNav from './SideNav.vue';
</script>

<template>
  <div class="app-layout">
    <TopNav />
    <div class="app-body">
      <SideNav />
      <main class="app-main">
        <div class="app-content">
          <RouterView v-slot="{ Component }">
            <Transition name="fade" mode="out-in">
              <component :is="Component" />
            </Transition>
          </RouterView>
        </div>
      </main>
    </div>
  </div>
</template>
