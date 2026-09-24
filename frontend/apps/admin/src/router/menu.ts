/**
 * 菜单树 —— 侧边栏与面包屑的唯一数据源
 *
 * 约定：
 *  1. 与 router/index.ts 中的 path 一一对应，改菜单必须同步改路由。
 *  2. roles 为空表示所有已登录用户可见；本期框架阶段角色权限为占位，
 *     后续接入 SSO + 后端菜单接口后，本文件降级为兜底数据。
 */

export interface MenuNode {
  /** 路由路径，同时作为菜单唯一 key */
  path: string;
  title: string;
  /** 图标名，见 utils/icons.ts */
  icon?: string;
  /** 子菜单；有值则为目录节点 */
  children?: MenuNode[];
  /** 允许访问的角色编码；为空表示不限制 */
  roles?: string[];
  /** 是否在侧边栏隐藏（如详情页入口） */
  hidden?: boolean;
}

export const menuTree: MenuNode[] = [
  { path: '/dashboard', title: '数据中心', icon: 'Odometer' },
  {
    path: '/production',
    title: '生产管理',
    icon: 'Grid',
    children: [
      { path: '/production/line', title: '产线主数据', icon: 'List' },
      { path: '/production/product', title: '班次与产品', icon: 'Box' }
    ]
  },
  {
    path: '/energy',
    title: '能源计量',
    icon: 'Lightning',
    children: [
      { path: '/energy/usage', title: '用量查询', icon: 'Search' },
      { path: '/energy/electric', title: '电计量', icon: 'DataLine' },
      { path: '/energy/water', title: '水计量', icon: 'DataAnalysis' },
      { path: '/energy/steam', title: '汽计量', icon: 'DataAnalysis' },
      { path: '/energy/report', title: '能源报表', icon: 'Document' }
    ]
  },
  {
    path: '/alarm',
    title: '告警管理',
    icon: 'Bell',
    children: [
      { path: '/alarm/realtime', title: '实时告警', icon: 'Warning' },
      { path: '/alarm/history', title: '历史告警', icon: 'Clock' },
      { path: '/alarm/rule', title: '告警规则', icon: 'SetUp' }
    ]
  },
  {
    path: '/device',
    title: '设备与采集',
    icon: 'Cpu',
    children: [
      { path: '/device/meter', title: '计量点位', icon: 'DataAnalysis' },
      { path: '/device/gateway', title: '采集网关', icon: 'Connection' },
      { path: '/device/readings', title: '仪表读数查询', icon: 'Histogram' },
      { path: '/device/quality', title: '数据质量', icon: 'CircleCheck' }
    ]
  },
  { path: '/video', title: '视频监控', icon: 'VideoCamera' },
  {
    path: '/log',
    title: '日志管理',
    icon: 'Tickets',
    children: [
      { path: '/log/collect', title: '数据采集日志', icon: 'Refresh' },
      { path: '/log/operation', title: '操作审计日志', icon: 'Operation' },
      { path: '/log/login', title: '登录日志', icon: 'Key' }
    ]
  },
  {
    path: '/system',
    title: '系统管理',
    icon: 'Setting',
    roles: ['super_admin', 'energy_admin'],
    children: [
      { path: '/system/user', title: '用户管理', icon: 'User' },
      { path: '/system/role', title: '角色管理', icon: 'UserFilled' },
      { path: '/system/menu', title: '菜单管理', icon: 'Menu' },
      { path: '/system/param', title: '系统参数', icon: 'Tools' },
      { path: '/system/dict', title: '数据字典', icon: 'Collection' }
    ]
  }
];

/** 扁平化菜单（用于按 path 查标题） */
export const flatMenu: MenuNode[] = menuTree.flatMap((m) => [m, ...(m.children ?? [])]);

/** 根据 path 生成面包屑，如 /energy/electric → ['能源计量', '电计量'] */
export function breadcrumbOf(path: string): string[] {
  for (const node of menuTree) {
    if (node.path === path) return [node.title];
    const child = node.children?.find((c) => c.path === path);
    if (child) return [node.title, child.title];
  }
  return [];
}
