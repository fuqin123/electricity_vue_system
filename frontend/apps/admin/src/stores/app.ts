import { ref } from 'vue';
import { defineStore } from 'pinia';

const COLLAPSE_KEY = 'eep_sidebar_collapsed';

/** 应用级 UI 状态：侧边栏折叠、页签缓存 */
export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = ref(localStorage.getItem(COLLAPSE_KEY) === '1');

  function toggleSidebar(): void {
    sidebarCollapsed.value = !sidebarCollapsed.value;
    localStorage.setItem(COLLAPSE_KEY, sidebarCollapsed.value ? '1' : '0');
  }

  return { sidebarCollapsed, toggleSidebar };
});
