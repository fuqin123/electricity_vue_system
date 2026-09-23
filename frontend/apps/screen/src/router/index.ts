import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router';

/**
 * 大屏路由（hash 模式）
 *   #/overview    → 大屏一：车间总览
 *   #/line/A1     → 大屏二：单线详情
 * 采用 hash 模式，静态目录任意层级部署均无需服务端 rewrite 配置。
 */
const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/overview' },
  {
    path: '/overview',
    name: 'overview',
    component: () => import('@/views/OverviewView.vue'),
    meta: { title: '车间总览' }
  },
  {
    path: '/line/:lineId',
    name: 'line',
    // 不用 props: true —— LineView 直接从 route.params 读，
    // 否则 lineId 会以普通 attribute 落到根元素上并触发 Vue 警告
    component: () => import('@/views/LineView.vue'),
    meta: { title: '单线详情' }
  },
  { path: '/:pathMatch(.*)*', redirect: '/overview' }
];

const router = createRouter({
  history: createWebHashHistory(),
  routes
});

router.afterEach((to) => {
  const base = '能源与产量数据中心';
  document.title = to.meta?.title ? `${to.meta.title} · ${base}` : base;
});

export default router;
