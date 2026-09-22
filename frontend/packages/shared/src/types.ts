/**
 * 领域类型定义 —— 大屏与后台共用
 *
 * 数据口径说明（与采集侧约定）：
 *  - 电量 kWh      ：产线配电柜智能电表（Modbus TCP 网关），电表为累计值，入库前需差分得到小时电量
 *  - 产量 瓶       ：灌装机计数器 / 装箱机 PLC / MES 班次报工
 *  - 用水 t        ：车间进水总表（电磁流量计），仅车间级，无法拆分到线别
 *  - 用汽 t        ：蒸汽总表（涡街/孔板流量计，带温压补偿），仅车间级
 */

/** 产线运行状态 */
export type LineStatus = 'running' | 'slow' | 'stopped';

/** 告警级别 */
export type AlertLevel = 'err' | 'warn' | 'info';

/** 趋势维度：年 / 月 / 日 / 时 */
export type TrendDim = '年' | '月' | '日' | '时';

/* ══════════════════════════════════════════════════════════════
   大屏数据模型
   ══════════════════════════════════════════════════════════════ */

/** 单条产线的小时数据点 */
export interface HourPoint {
  /** 0—23 */
  hour: number;
  /** 产量（瓶）；未上报或未到时段为 null */
  output: number | null;
  /** 用电量（kWh）；未上报或未到时段为 null，刚过整点可能带 1 位小数 */
  kwh: number | null;
  /** 单位电耗（kWh/千瓶）；产量为 0 或未上报时为 null */
  unitKwh: number | null;
  /** 计划停机窗口（CIP / 换型 / 检修） */
  stopped?: boolean;
  /** 未到时段（预测态，前端渲染为虚线柱） */
  future?: boolean;
  /** 正在进行中的小时（只累计了部分分钟） */
  partial?: boolean;
  /** 整点预期产量，仅 partial 时有值，用于状态判定与展示 */
  projected?: number;
  /** 已过时间占比 0—1，仅 partial 时有值 */
  progress?: number;
}

/** 趋势点（日 / 月 / 年） */
export interface TrendPoint {
  label: string;
  output: number;
  kwh: number;
  unitKwh?: number | null;
}

/** 单条产线的聚合统计 */
export interface LineData {
  id: string;
  name: string;
  /** 额定产能（瓶/小时） */
  rated: number;
  product: string;
  status: LineStatus;
  /** 停机原因备注，如「CIP 清洗停机」 */
  note: string;
  hours: HourPoint[];
  today: {
    output: number;
    kwh: number;
    unitKwh: number | null;
    /** 截至当前小时的应产（日达成率分母） */
    plan: number;
    /** 全天计划 */
    planFull: number;
    rate: number;
  };
  yesterday: {
    output: number;
    kwh: number;
    unitKwh: number | null;
    fullOutput: number;
    fullKwh: number;
  };
  daily30: TrendPoint[];
  /** 近 7 日 × 24 小时产量，单位「百瓶」，未上报为 null */
  heat: (number | null)[][];
  heatDays: string[];
  month: { output: number; kwh: number };
  year: { output: number; kwh: number; full: number; fullKwh: number };
  dayOfYear: number;
}

/** 告警条目 */
export interface AlertItem {
  level: AlertLevel;
  text: string;
  time: string;
}

/** 水 / 汽 单项用量 */
export interface UtilityMetric {
  today: number;
  yesterday: number;
  /** 当前瞬时流量 t/h */
  rateNow: number;
  unit: string;
  /** 24 小时趋势，未到时段为 null */
  trend: (number | null)[];
}

/** 车间级聚合数据 */
export interface WorkshopData {
  lines: LineData[];
  today: {
    output: number;
    kwh: number;
    unitKwh: number;
    plan: number;
    waterT: number;
    steamT: number;
  };
  yesterday: {
    output: number;
    kwh: number;
    unitKwh: number;
    waterT: number;
    steamT: number;
  };
  month: { output: number; kwh: number };
  year: { output: number; kwh: number };
  hours: HourPoint[];
  daily30: TrendPoint[];
  monthly12: TrendPoint[];
  yearly5: TrendPoint[];
  utility: { water: UtilityMetric; steam: UtilityMetric };
  /** 单位电耗考核目标 kWh/千瓶 */
  targetUnitKwh: number;
  alerts: AlertItem[];
  currentHour: number;
}

/** 大屏顶层快照 —— 后端接口需返回同构对象 */
export interface EnergySnapshot {
  now: Date;
  workshop: WorkshopData;
}

/* ══════════════════════════════════════════════════════════════
   后台管理数据模型（本期为虚拟数据，字段与后续接口保持同构）
   ══════════════════════════════════════════════════════════════ */

/** 通用分页查询参数 */
export interface PageQuery {
  page: number;
  pageSize: number;
  keyword?: string;
  [key: string]: unknown;
}

/** 通用分页返回结构 */
export interface PageResult<T> {
  list: T[];
  total: number;
  page: number;
  pageSize: number;
}

/** 登录用户 */
export interface SysUser {
  id: number;
  username: string;
  nickname: string;
  /** 所属工厂/车间 */
  deptName: string;
  roleName: string;
  phone: string;
  email: string;
  status: 'enabled' | 'disabled';
  lastLoginAt: string;
  createdAt: string;
}

/** 角色 */
export interface SysRole {
  id: number;
  code: string;
  name: string;
  /** 绑定用户数 */
  userCount: number;
  remark: string;
  createdAt: string;
}

/** 菜单 / 权限项 */
export interface SysMenu {
  id: number;
  parentId: number;
  name: string;
  /** 路由路径或权限标识 */
  path: string;
  icon: string;
  type: 'catalog' | 'menu' | 'button';
  sort: number;
  visible: boolean;
}

/** 产线主数据（后台维护，大屏读取） */
export interface LineMaster {
  id: number;
  code: string;
  name: string;
  product: string;
  /** 额定产能 瓶/小时 */
  rated: number;
  workshop: string;
  meterNo: string;
  counterNo: string;
  status: 'running' | 'idle' | 'maintenance';
  commissionedAt: string;
}

/** 计量点位 */
export interface MeterPoint {
  id: number;
  code: string;
  name: string;
  category: '电' | '水' | '汽' | '气';
  lineCode: string;
  protocol: 'Modbus TCP' | 'Modbus RTU' | 'OPC UA' | 'MQTT' | 'DL/T645';
  address: string;
  unit: string;
  factor: number;
  status: 'online' | 'offline' | 'fault';
  lastAt: string;
}

/** 采集任务 / 网关 */
export interface Gateway {
  id: number;
  name: string;
  ip: string;
  port: number;
  protocol: string;
  pointCount: number;
  status: 'online' | 'offline';
  lastHeartbeat: string;
}

/** 告警记录 */
export interface AlarmRecord {
  id: number;
  code: string;
  level: 'critical' | 'major' | 'minor' | 'info';
  source: string;
  content: string;
  /** 触发值 / 阈值 */
  value: string;
  threshold: string;
  status: 'active' | 'acked' | 'resolved';
  occurredAt: string;
  ackedBy: string;
  ackedAt: string;
}

/** 数据采集日志 */
export interface CollectLog {
  id: number;
  taskName: string;
  gateway: string;
  pointCount: number;
  /** 影响行数 */
  rows: number;
  durationMs: number;
  result: 'success' | 'partial' | 'failed';
  message: string;
  startedAt: string;
}

/** 操作审计日志 */
export interface OperationLog {
  id: number;
  username: string;
  module: string;
  action: string;
  target: string;
  ip: string;
  ua: string;
  result: 'success' | 'failed';
  operatedAt: string;
}

/** 登录日志 */
export interface LoginLog {
  id: number;
  username: string;
  ip: string;
  location: string;
  browser: string;
  os: string;
  result: 'success' | 'failed';
  message: string;
  loginAt: string;
}

/** 系统参数 */
export interface SysParam {
  id: number;
  key: string;
  name: string;
  value: string;
  unit: string;
  group: string;
  remark: string;
}

/** 字典项 */
export interface DictItem {
  id: number;
  dictType: string;
  label: string;
  value: string;
  sort: number;
  status: 'enabled' | 'disabled';
  remark: string;
}

/** 报表模板 */
export interface ReportTemplate {
  id: number;
  name: string;
  cycle: '日报' | '周报' | '月报' | '年报';
  scope: string;
  format: 'xlsx' | 'pdf' | 'csv';
  enabled: boolean;
  lastGeneratedAt: string;
}

/** 数据质量统计（按产线） */
export interface DataQualityRow {
  lineCode: string;
  lineName: string;
  expectPoints: number;
  receivedPoints: number;
  missingPoints: number;
  rate: number;
  missingHours: string;
  lastAt: string;
}

/** 后台首页看板指标 */
export interface AdminOverview {
  kpi: {
    todayOutput: number;
    todayKwh: number;
    unitKwh: number;
    activeAlarms: number;
    onlineGateways: number;
    totalGateways: number;
    dataQualityRate: number;
  };
  /** 今日分时（车间合计） */
  hourly: TrendPoint[];
  /** 近 30 日 */
  daily30: TrendPoint[];
  /** 近 12 个月 */
  monthly12: TrendPoint[];
  /** 各线单位电耗对标 */
  lineUnitKwh: { lineCode: string; lineName: string; unitKwh: number; output: number }[];
  /** 最新告警 */
  latestAlarms: AlarmRecord[];
}
