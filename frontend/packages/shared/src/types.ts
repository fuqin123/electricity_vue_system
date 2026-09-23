/**
 * 领域类型定义 —— 大屏与后台共用
 *
 * 数据口径说明（与采集侧约定）：
 *  - 电量 kWh      ：产线配电柜智能电表（Modbus TCP 网关），电表为累计值，入库前需差分得到小时电量
 *  - 产量 瓶       ：灌装机计数器 / 装箱机 PLC / MES 班次报工
 *  - 用水 t        ：车间进水总表（电磁流量计），仅车间级，无法拆分到线别
 *  - 用汽 t        ：蒸汽总表（涡街/孔板流量计，带温压补偿），仅车间级
 */

/**
 * 产线运行状态
 *
 * ⚠ `running` / `slow` / `stopped` 在**产量未接入**时的含义（v1.2.1 起）：
 *   产量数据源尚未改造，无法用产量判定产能利用率，
 *   因此这三个状态退化为**按用电负荷判定**：
 *     running —— 本小时有用电（≥ 该线基础负荷的 60%）
 *     slow    —— 本小时有用电但明显低于基础负荷（可能在停机/待机）
 *     stopped —— 本小时无任何用电
 *   前端会在产量未接入时同时标注「产量未接入」，避免把状态误读成产量结论。
 */
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
  /** 整点预期产量，仅 partial 时有值，用于状态判定与展示；产量未接入时为 null */
  projected?: number | null;
  /** 已过时间占比 0—1，仅 partial 时有值 */
  progress?: number;
}

/** 趋势点（日 / 月 / 年）
 *  ⚠ `output` 允许为 null：产量数据源未接入（演示占位）时一律为 null，
 *    界面必须显示 `--`，不能把 null 当 0 —— 0 会被读成「确实没产出」。
 */
export interface TrendPoint {
  label: string;
  output: number | null;
  /**
   * 用电量。该周期没有任何可信小时差值时为 **null**（界面留空 / 显示 `--`），不是 0。
   * ⚠ 曾经声明为 `number`，于是 `x.kwh.toFixed()` 能通过类型检查并在运行时抛异常，
   *   把整张趋势图带走（B-513）；把 null 兜成 0 又会画出「用量暴跌」的假曲线（B-106）。
   */
  kwh: number | null;
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
    /** 产量未接入时为 null（显示 --），不是 0 */
    output: number | null;
    kwh: number;
    unitKwh: number | null;
    /** 截至当前小时的应产（日达成率分母） */
    plan: number;
    /** 全天计划 */
    planFull: number;
    rate: number;
    /**
     * 今日用电的采集覆盖度（已覆盖小时 / 应有小时）。
     * 进行中的周期不再因为「有 1 个小时缺数」就返回 null ——
     * 但必须把覆盖度显示出来，否则刚上线时「今日累计」会被读成「今天几乎没用电」。
     */
    kwhCoveredHours?: number;
    kwhExpectedHours?: number;
  };
  yesterday: {
    /** 产量未接入时为 null（显示 --），不是 0 */
    output: number | null;
    kwh: number;
    unitKwh: number | null;
    fullOutput: number | null;
    fullKwh: number;
  };
  /**
   * 近 30 日（**按日期正序**，末项 = 今日；X 轴从左向右读时间）
   * ⚠ 字段名里的 30 是桶数上限，语义是「日维度的趋势序列」，改名会牵动接口契约。
   */
  daily30: TrendPoint[];
  /** 近 12 个月（与总览同口径，供线别页的「月」维度切换；同样正序） */
  monthly12: TrendPoint[];
  /** 近 5 年（供线别页的「年」维度切换；同样正序，末项 = 今年） */
  yearly5: TrendPoint[];
  /** 近 7 日 × 24 小时产量，单位「百瓶」，未上报为 null */
  heat: (number | null)[][];
  heatDays: string[];
  month: { output: number | null; kwh: number };
  year: { output: number | null; kwh: number; full: number | null; fullKwh: number };
  dayOfYear: number;
}

/** 告警条目 */
export interface AlertItem {
  level: AlertLevel;
  text: string;
  time: string;
}

/** 水 / 汽 / 电量 单项用量
 *  ⚠ `today` / `yesterday` 允许为 null：取不到数就是未知，**不是 0**。
 *    后端在缺数时返回 null，前端必须显示 `--` 而不是 0。
 */
export interface UtilityMetric {
  today: number | null;
  yesterday: number | null;
  /** 当前瞬时流量 t/h */
  rateNow: number;
  unit: string;
  /** 24 小时趋势，未到时段为 null */
  trend: (number | null)[];
  /** 今日累计的采集覆盖度（已覆盖小时 / 应有小时） */
  coveredHours?: number;
  expectedHours?: number;
}

/** 车间级聚合数据 */
export interface WorkshopData {
  lines: LineData[];
  today: {
    /** 产量未接入时为 null（显示 --），不是 0 */
    output: number | null;
    kwh: number;
    /** 产量未接入时为 null（算不出来），不要用 0 兜底 —— 0 会被读成「能效完美」 */
    unitKwh: number | null;
    plan: number;
    waterT: number | null;
    steamT: number | null;
    /** 今日用电的采集覆盖度，含义同 LineData.today */
    kwhCoveredHours?: number;
    kwhExpectedHours?: number;
  };
  yesterday: {
    /** 产量未接入时为 null（显示 --），不是 0 */
    output: number | null;
    kwh: number;
    unitKwh: number | null;
    waterT: number | null;
    steamT: number | null;
  };
  month: { output: number | null; kwh: number };
  year: { output: number | null; kwh: number };
  hours: HourPoint[];
  daily30: TrendPoint[];
  monthly12: TrendPoint[];
  yearly5: TrendPoint[];
  utility: { water: UtilityMetric; steam: UtilityMetric };
  /** 单位电耗考核目标 kWh/千瓶（产量接入后才能标定） */
  targetUnitKwh: number;
  alerts: AlertItem[];
  currentHour: number;
}

/** 大屏顶层快照 —— 后端接口需返回同构对象 */
export interface EnergySnapshot {
  now: Date;
  workshop: WorkshopData;
  /**
   * 数据来源：
   *   'api'  —— 真实接口（现场采集数据）
   *   'mock' —— 演示数据。**只在后端不可达时降级产生**，
   *             此时 `notice` 一定有值，界面必须显示出来。
   *             绝不能把演示数据当真实数据展示（现场会据此做生产判断）。
   */
  source?: 'api' | 'mock';
  /** source = 'mock' 时的原因说明 */
  notice?: string;
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

/**
 * 计量点位
 *
 * 字段分三段：
 *   ① 列表展示（category / address / protocol / status …）—— 后端按网关类型拼好，页面直接用；
 *   ② 可编辑配置（kind / slave / register / scale …）—— 后台表单回填与提交用；
 *   ③ 最近状态（lastValue / lastOk / todayUsage）—— 便于现场核对。
 * 与后端 `apps/energy/api.py` 的 `meter_json()` **一一对应**，改一边必须改另一边。
 */
export interface MeterPoint {
  /* ① 列表展示 */
  id: number;
  code: string;
  name: string;
  category: '电' | '水' | '汽' | '气';
  lineCode: string;
  protocol: string;
  address: string;
  unit: string;
  factor: number;
  status: 'online' | 'offline' | 'fault';
  lastAt: string;

  /* ② 可编辑配置 */
  kind: 'elec' | 'water' | 'steam';
  scope: 'line' | 'workshop';
  role: 'line' | 'aux' | 'total';
  gatewayId: number;
  gatewayCode: string;
  /** Modbus 从站号（串口服务器类型必填） */
  slave: number | null;
  /** 寄存器起始地址（串口服务器类型必填） */
  register: number | null;
  registers: number;
  funcCode: number;
  wordOrder: 'big' | 'little';
  /** HTTP API 类型的接口路径 */
  urlPath: string;
  /** HTTP API 类型的请求参数（如 { cid: 135 }） */
  params: Record<string, unknown>;
  /** 累计值在响应里的取值路径，如 data.0.device_data.var1 */
  valuePath: string;
  /** 采集时间在响应里的取值路径 */
  timePath: string;
  /** 小时用量上限，超过判为异常尖峰 */
  maxHourly: number | null;
  enabled: boolean;
  /** 'config' 配置文件登记 / 'admin' 后台维护（后台改过的不会被 seed_registry 覆盖） */
  source: 'config' | 'admin';
  remark: string;
  assetNo: string;
  installDate: string;

  /* ③ 最近状态 */
  lastValue: number | null;
  lastOk: boolean;
  lastError: string;
  /** 今日已累计用量（只算可信小时） */
  todayUsage: number | null;
  todayHours: number;
}

/**
 * 采集网关
 *
 * 串口服务器（Modbus RTU over TCP）与第三方数据平台（HTTP API）共用一张表，
 * 靠 `kind` 区分；`host/port` 给串口服务器用，`baseUrl` 给 HTTP 平台用。
 */
export interface Gateway {
  id: number;
  code: string;
  name: string;
  kind: 'modbus_rtu_tcp' | 'http_api';
  protocol: string;
  ip: string;
  port: number | null;
  baseUrl: string;
  /** 一行地址摘要，日志与排障用 */
  endpoint: string;
  pointCount: number;
  status: 'online' | 'offline';
  lastHeartbeat: string;
  timeoutMs: number;
  intervalSeconds: number;
  enabled: boolean;
  source: 'config' | 'admin';
  remark: string;
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
    /** 产量未接入时为 null（显示 --），不是 0 */
    todayOutput: number | null;
    todayKwh: number;
    /** 产量未接入时为 null（算不出来），不要用 0 兜底 */
    unitKwh: number | null;
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
  lineUnitKwh: { lineCode: string; lineName: string; unitKwh: number | null; output: number | null }[];
  /** 最新告警 */
  latestAlarms: AlarmRecord[];
}
