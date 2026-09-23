import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import { TOKEN_KEY } from '@eep/shared';
import AdminLayout from '@/layout/AdminLayout.vue';

/**
 * 后台管理路由（history 模式）
 *
 * ⚠ 部署要求：Nginx / Apache 需配置 history 回退（try_files / FallbackResource），
 *   否则刷新子路由会 404。详见 docs/02-版本部署-Linux.md。
 *
 * 本期为框架阶段：路由、菜单、页面骨架、跳转全部打通，业务函数留空。
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/login/LoginView.vue'),
    meta: { title: '登录', public: true }
  },
  {
    path: '/',
    component: AdminLayout,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/dashboard/DashboardView.vue'),
        meta: { title: '数据中心', desc: '车间能源与产量总览，数据每 60 秒自动刷新' }
      },

      /* ── 生产管理 ── */
      {
        path: 'production/line',
        name: 'production-line',
        component: () => import('@/views/production/LineListView.vue'),
        meta: { title: '产线主数据', desc: '维护产线编号、产品、额定产能与计量点位绑定关系' }
      },
      {
        path: 'production/product',
        name: 'production-product',
        component: () => import('@/views/production/ProductView.vue'),
        meta: { title: '班次与产品', desc: '班次时段定义与产品主数据' }
      },

      /* ── 能源计量 ── */
      {
        path: 'energy/usage',
        name: 'energy-usage',
        component: () => import('@/views/energy/UsageQueryView.vue'),
        meta: { title: '用量查询' },
      },
      {
        path: 'energy/electric',
        name: 'energy-electric',
        component: () => import('@/views/energy/ElectricView.vue'),
        meta: { title: '电计量', desc: '产线配电柜智能电表的整点电量与单位电耗' }
      },
      {
        path: 'energy/water',
        name: 'energy-water',
        component: () => import('@/views/energy/WaterView.vue'),
        meta: { title: '水计量', desc: '车间进水总表数据（仅车间级，不可拆分至线别）' }
      },
      {
        path: 'energy/steam',
        name: 'energy-steam',
        component: () => import('@/views/energy/SteamView.vue'),
        meta: { title: '汽计量', desc: '车间蒸汽总表数据（需带温压补偿）' }
      },
      {
        path: 'energy/report',
        name: 'energy-report',
        component: () => import('@/views/energy/ReportView.vue'),
        meta: { title: '能源报表', desc: '日报 / 周报 / 月报 / 年报模板与生成记录' }
      },

      /* ── 告警管理 ── */
      {
        path: 'alarm/realtime',
        name: 'alarm-realtime',
        component: () => import('@/views/alarm/RealtimeView.vue'),
        meta: { title: '实时告警', desc: '未处理告警，支持批量确认' }
      },
      {
        path: 'alarm/history',
        name: 'alarm-history',
        component: () => import('@/views/alarm/HistoryView.vue'),
        meta: { title: '历史告警', desc: '所有告警记录与处理结果回溯' }
      },
      {
        path: 'alarm/rule',
        name: 'alarm-rule',
        component: () => import('@/views/alarm/RuleView.vue'),
        meta: { title: '告警规则', desc: '阈值与触发条件配置' }
      },

      /* ── 设备与采集 ── */
      {
        path: 'device/meter',
        name: 'device-meter',
        component: () => import('@/views/device/MeterView.vue'),
        meta: { title: '计量点位', desc: '电 / 水 / 汽 / 气 计量点位的协议、地址与系数' }
      },
      {
        path: 'device/gateway',
        name: 'device-gateway',
        component: () => import('@/views/device/GatewayView.vue'),
        meta: { title: '采集网关', desc: '网关在线状态与心跳监控' }
      },
      {
        path: 'device/quality',
        name: 'device-quality',
        component: () => import('@/views/device/QualityView.vue'),
        meta: { title: '数据质量', desc: '各产线整点数据的应报 / 实收 / 缺失统计' }
      },

      /* ── 视频监控 ── */
      {
        path: 'video',
        name: 'video',
        component: () => import('@/views/video/VideoView.vue'),
        meta: { title: '视频监控', desc: 'RTSP 转流网关状态与通道配置' }
      },

      /* ── 日志管理 ── */
      {
        path: 'log/collect',
        name: 'log-collect',
        component: () => import('@/views/log/CollectLogView.vue'),
        meta: { title: '数据采集日志', desc: '任务开始 / 结束时间、执行结果与影响行数' }
      },
      {
        path: 'log/operation',
        name: 'log-operation',
        component: () => import('@/views/log/OperationLogView.vue'),
        meta: { title: '操作审计日志', desc: '关键操作的作者、时间、对象与结果' }
      },
      {
        path: 'log/login',
        name: 'log-login',
        component: () => import('@/views/log/LoginLogView.vue'),
        meta: { title: '登录日志', desc: '登录来源、终端与失败原因' }
      },

      /* ── 系统管理 ── */
      {
        path: 'system/user',
        name: 'system-user',
        component: () => import('@/views/system/UserView.vue'),
        meta: { title: '用户管理', desc: '账号、角色归属与状态（正式环境走统一身份认证）' }
      },
      {
        path: 'system/role',
        name: 'system-role',
        component: () => import('@/views/system/RoleView.vue'),
        meta: { title: '角色管理', desc: '角色定义与菜单 / 按钮权限分配' }
      },
      {
        path: 'system/menu',
        name: 'system-menu',
        component: () => import('@/views/system/MenuView.vue'),
        meta: { title: '菜单管理', desc: '菜单树结构、路由路径与可见性' }
      },
      {
        path: 'system/param',
        name: 'system-param',
        component: () => import('@/views/system/ParamView.vue'),
        meta: { title: '系统参数', desc: '能耗阈值、采集策略、大屏参数与运维策略' }
      },
      {
        path: 'system/dict',
        name: 'system-dict',
        component: () => import('@/views/system/DictView.vue'),
        meta: { title: '数据字典', desc: '枚举值统一维护，供下拉与状态展示引用' }
      }
    ]
  },

  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/error/NotFound.vue'),
    meta: { title: '页面不存在', public: true }
  }
];

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 })
});

/** 登录态守卫：本期仅校验本地 token，后续替换为后端权限校验 */
router.beforeEach((to) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (to.meta?.public) return true;
  if (!token) return { name: 'login', query: { redirect: to.fullPath } };
  return true;
});

router.afterEach((to) => {
  const base = '能源产效管理系统';
  document.title = to.meta?.title ? `${to.meta.title as string} · ${base}` : base;
});

export default router;
