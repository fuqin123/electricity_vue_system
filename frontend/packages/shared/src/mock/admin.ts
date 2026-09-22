/**
 * 后台管理 mock 数据
 *
 * 本期所有业务函数留空，仅提供与后续真实接口同构的虚拟数据，
 * 用于打通「路由 / 菜单 / 页面骨架 / 增删改查交互」。
 *
 * ⚠ 替换真实接口时，只需改动 api 适配层（api/index.ts），页面代码无需变更。
 */

import { linesConfig } from '../config';
import { isoDateTime, makeRng, pad2, pick, round2 } from '../format';
import { buildSnapshot } from './energy';
import type {
  AdminOverview,
  AlarmRecord,
  CollectLog,
  DataQualityRow,
  DictItem,
  Gateway,
  LineMaster,
  LoginLog,
  MeterPoint,
  OperationLog,
  PageQuery,
  PageResult,
  ReportTemplate,
  SysMenu,
  SysParam,
  SysRole,
  SysUser
} from '../types';

const WORKSHOPS = ['灌装一车间', '灌装二车间', '调配车间'] as const;
const NICKNAMES = ['张伟', '李娜', '王强', '刘洋', '陈静', '赵磊', '孙倩', '周涛', '吴敏', '郑凯'];
const PREFIX = ['zhangwei', 'lina', 'wangqiang', 'liuyang', 'chenjing', 'zhaolei', 'sunqian', 'zhoutao', 'wumin', 'zhengkai'];

/** 基准时间：以运行时刻为准，保证数据看起来是「最近的」 */
function baseTime(): Date {
  const d = new Date();
  d.setSeconds(0, 0);
  return d;
}

function priorTime(minutesAgo: number): string {
  return isoDateTime(new Date(baseTime().getTime() - minutesAgo * 60000));
}

/* ══════════════════════════════════════════════════════════════
   系统管理
   ══════════════════════════════════════════════════════════════ */

export function mockUsers(): SysUser[] {
  const rng = makeRng('users');
  return NICKNAMES.map((nickname, i) => ({
    id: i + 1,
    username: PREFIX[i],
    nickname,
    deptName: pick(WORKSHOPS, rng),
    roleName: i === 0 ? '超级管理员' : i < 3 ? '能源管理员' : i < 6 ? '产线操作员' : '只读用户',
    phone: `13${(i % 9) + 1}${pad2(i)}${pad2(37 + i)}${pad2(11 + i)}`,
    email: `${PREFIX[i]}@plant01.local`,
    status: i === 8 ? 'disabled' : 'enabled',
    lastLoginAt: priorTime(30 + i * 137),
    createdAt: `2026-0${(i % 8) + 1}-1${(i % 9) + 1} 09:2${i % 9}:11`
  }));
}

export function mockRoles(): SysRole[] {
  return [
    { id: 1, code: 'super_admin', name: '超级管理员', userCount: 1, remark: '拥有全部菜单与按钮权限', createdAt: '2026-01-05 09:20:11' },
    { id: 2, code: 'energy_admin', name: '能源管理员', userCount: 2, remark: '能源计量、报表、阈值参数配置', createdAt: '2026-01-05 09:24:03' },
    { id: 3, code: 'line_operator', name: '产线操作员', userCount: 3, remark: '产线数据查看、告警确认', createdAt: '2026-01-06 10:11:47' },
    { id: 4, code: 'viewer', name: '只读用户', userCount: 4, remark: '仅可查看大屏与列表，无写权限', createdAt: '2026-01-06 10:15:22' },
    { id: 5, code: 'device_engineer', name: '设备工程师', userCount: 0, remark: '计量点位与网关维护', createdAt: '2026-02-11 14:02:59' }
  ];
}

/** 菜单树：与路由保持一致，仅用于系统管理页展示 */
export function mockMenus(): SysMenu[] {
  // [id, parentId, 名称, 路由路径, 图标标识, 类型]
  const defs: Array<[number, number, string, string, string, SysMenu['type']]> = [
    [1, 0, '数据中心', '/dashboard', 'dashboard', 'menu'],
    [2, 0, '生产管理', '/production', 'factory', 'catalog'],
    [3, 2, '产线主数据', '/production/line', 'list', 'menu'],
    [4, 2, '班次与产品', '/production/product', 'tag', 'menu'],
    [5, 0, '能源计量', '/energy', 'flash', 'catalog'],
    [6, 5, '电计量', '/energy/electric', 'bolt', 'menu'],
    [7, 5, '水计量', '/energy/water', 'water', 'menu'],
    [8, 5, '汽计量', '/energy/steam', 'steam', 'menu'],
    [9, 5, '能源报表', '/energy/report', 'document', 'menu'],
    [10, 0, '告警管理', '/alarm', 'bell', 'catalog'],
    [11, 10, '实时告警', '/alarm/realtime', 'alert', 'menu'],
    [12, 10, '历史告警', '/alarm/history', 'history', 'menu'],
    [13, 10, '告警规则', '/alarm/rule', 'setting', 'menu'],
    [14, 0, '设备与采集', '/device', 'cpu', 'catalog'],
    [15, 14, '计量点位', '/device/meter', 'gauge', 'menu'],
    [16, 14, '采集网关', '/device/gateway', 'link', 'menu'],
    [17, 14, '数据质量', '/device/quality', 'check', 'menu'],
    [18, 0, '视频监控', '/video', 'camera', 'menu'],
    [19, 0, '日志管理', '/log', 'file', 'catalog'],
    [20, 19, '数据采集日志', '/log/collect', 'sync', 'menu'],
    [21, 19, '操作审计日志', '/log/operation', 'audit', 'menu'],
    [22, 19, '登录日志', '/log/login', 'key', 'menu'],
    [23, 0, '系统管理', '/system', 'setting', 'catalog'],
    [24, 23, '用户管理', '/system/user', 'user', 'menu'],
    [25, 23, '角色管理', '/system/role', 'team', 'menu'],
    [26, 23, '菜单管理', '/system/menu', 'menu', 'menu'],
    [27, 23, '系统参数', '/system/param', 'param', 'menu'],
    [28, 23, '数据字典', '/system/dict', 'book', 'menu']
  ];
  return defs.map(([id, parentId, name, path, icon, type], i) => ({
    id,
    parentId,
    name,
    path,
    icon,
    type,
    sort: (i + 1) * 10,
    visible: true
  }));
}

export function mockParams(): SysParam[] {
  return [
    { id: 1, key: 'energy.target.unitKwh', name: '单位电耗考核目标', value: '8.20', unit: 'kWh/千瓶', group: '能耗阈值', remark: '车间合计口径，低于该值为达标' },
    { id: 2, key: 'energy.alarm.unitKwhHigh', name: '单位电耗告警上限', value: '9.60', unit: 'kWh/千瓶', group: '能耗阈值', remark: '超出触发严重告警' },
    { id: 3, key: 'energy.alarm.outputLowRate', name: '低产判定比例', value: '0.75', unit: '—', group: '能耗阈值', remark: '小时产量低于额定值的该比例视为低产' },
    { id: 4, key: 'collect.interval', name: '采集周期', value: '60', unit: 's', group: '采集策略', remark: '整点聚合前的原始采样周期' },
    { id: 5, key: 'collect.retry', name: '失败重试次数', value: '3', unit: '次', group: '采集策略', remark: '外部调用异常时的重试上限，建议配合退避策略' },
    { id: 6, key: 'collect.timeout', name: '单次采集超时', value: '5000', unit: 'ms', group: '采集策略', remark: 'Modbus 读写超时' },
    { id: 7, key: 'screen.refreshMs', name: '大屏刷新间隔', value: '30000', unit: 'ms', group: '大屏参数', remark: '低于 5000ms 会被下限保护' },
    { id: 8, key: 'log.retentionDays', name: '本地日志保留天数', value: '30', unit: '天', group: '运维策略', remark: '按大小滚动，超期自动清理' },
    { id: 9, key: 'alarm.notify.channels', name: '告警通知渠道', value: 'mail,wecom', unit: '—', group: '告警通知', remark: '邮件 / 企业微信，逗号分隔' }
  ];
}

export function mockDicts(): DictItem[] {
  const defs: Array<[string, string[]]> = [
    ['line_status', ['运行中:running', '低产:slow', '停线:stopped', '维护中:maintenance']],
    ['alarm_level', ['严重:critical', '重要:major', '次要:minor', '提示:info']],
    ['meter_category', ['电:elec', '水:water', '汽:steam', '气:air']],
    ['protocol', ['Modbus TCP:modbus_tcp', 'Modbus RTU:modbus_rtu', 'OPC UA:opcua', 'MQTT:mqtt', 'DL/T645:dlt645']],
    ['report_cycle', ['日报:daily', '周报:weekly', '月报:monthly', '年报:yearly']]
  ];
  const out: DictItem[] = [];
  let id = 1;
  defs.forEach(([dictType, items]) => {
    items.forEach((it, i) => {
      const [label, value = ''] = it.split(':');
      out.push({
        id: id++,
        dictType,
        label,
        value,
        sort: (i + 1) * 10,
        status: 'enabled',
        remark: ''
      });
    });
  });
  return out;
}

/* ══════════════════════════════════════════════════════════════
   生产 / 设备
   ══════════════════════════════════════════════════════════════ */

export function mockLines(): LineMaster[] {
  const statusMap: LineMaster['status'][] = ['running', 'running', 'running', 'maintenance', 'running', 'idle'];
  return linesConfig.map((l, i) => ({
    id: i + 1,
    code: l.id,
    name: l.name,
    product: l.product,
    rated: l.rated,
    workshop: i < 3 ? '灌装一车间' : '灌装二车间',
    meterNo: `EM-${l.id}-${1001 + i}`,
    counterNo: `CT-${l.id}-${2001 + i}`,
    status: statusMap[i],
    commissionedAt: `2019-0${(i % 8) + 1}-1${(i % 9) + 1}`
  }));
}

export function mockMeters(): MeterPoint[] {
  const out: MeterPoint[] = [];
  let id = 1;
  linesConfig.forEach((l, i) => {
    out.push({
      id: id++,
      code: `EM-${l.id}-${1001 + i}`,
      name: `${l.name} 配电柜电表`,
      category: '电',
      lineCode: l.id,
      protocol: i % 2 === 0 ? 'Modbus TCP' : 'DL/T645',
      address: `10.20.31.${11 + Math.floor(i / 2)}:502`,
      unit: 'kWh',
      factor: 1,
      status: i === 4 ? 'fault' : 'online',
      lastAt: priorTime(i === 4 ? 96 : 2 + i)
    });
  });
  out.push(
    { id: id++, code: 'WM-TOTAL-01', name: '车间进水总表', category: '水', lineCode: '—', protocol: 'Modbus TCP', address: '10.20.31.21:502', unit: 't', factor: 0.1, status: 'online', lastAt: priorTime(3) },
    { id: id++, code: 'SM-TOTAL-01', name: '车间蒸汽总表', category: '汽', lineCode: '—', protocol: 'Modbus TCP', address: '10.20.31.22:502', unit: 't', factor: 0.01, status: 'online', lastAt: priorTime(3) },
    { id: id++, code: 'AM-TOTAL-01', name: '压缩空气总表', category: '气', lineCode: '—', protocol: 'Modbus RTU', address: 'COM3 / 9600-8-N-1', unit: 'm³', factor: 0.1, status: 'offline', lastAt: priorTime(2880) }
  );
  return out;
}

export function mockGateways(): Gateway[] {
  return [
    { id: 1, name: '灌装一车间网关', ip: '10.20.31.11', port: 502, protocol: 'Modbus TCP', pointCount: 24, status: 'online', lastHeartbeat: priorTime(1) },
    { id: 2, name: '灌装二车间网关', ip: '10.20.31.12', port: 502, protocol: 'Modbus TCP', pointCount: 22, status: 'online', lastHeartbeat: priorTime(1) },
    { id: 3, name: '公用工程网关', ip: '10.20.31.13', port: 502, protocol: 'Modbus TCP', pointCount: 12, status: 'online', lastHeartbeat: priorTime(2) },
    { id: 4, name: '空压站网关', ip: '10.20.31.14', port: 4840, protocol: 'OPC UA', pointCount: 8, status: 'offline', lastHeartbeat: priorTime(1440) },
    { id: 5, name: '视频转流网关', ip: '10.20.31.200', port: 8080, protocol: 'HTTP-FLV', pointCount: 6, status: 'online', lastHeartbeat: priorTime(1) }
  ];
}

/* ══════════════════════════════════════════════════════════════
   告警
   ══════════════════════════════════════════════════════════════ */

export function mockAlarms(): AlarmRecord[] {
  const defs: Array<Partial<AlarmRecord> & { content: string; source: string; level: AlarmRecord['level'] }> = [
    { code: 'ALM-20260922-0001', level: 'critical', source: 'A4 生产线', content: '单位电耗超告警上限', value: '10.24', threshold: '9.60', status: 'active' },
    { code: 'ALM-20260922-0002', level: 'major', source: 'A6 生产线', content: '连续 4 小时无产量，疑似停机', value: '0', threshold: '—', status: 'active' },
    { code: 'ALM-20260922-0003', level: 'major', source: 'EM-A5-1005', content: '电表通讯中断', value: '—', threshold: '心跳 60s', status: 'acked' },
    { code: 'ALM-20260922-0004', level: 'minor', source: 'A3 生产线', content: 'CIP 清洗期间辅机负荷偏高', value: '18.6', threshold: '15.0', status: 'acked' },
    { code: 'ALM-20260922-0005', level: 'minor', source: '车间蒸汽总表', content: '汽耗较昨日同期偏高', value: '+12.4%', threshold: '±10%', status: 'resolved' },
    { code: 'ALM-20260922-0006', level: 'info', source: '视频通道', content: '6 路通道未配置转流网关', value: '6', threshold: '0', status: 'active' },
    { code: 'ALM-20260921-0017', level: 'minor', source: 'A1 生产线', content: '单位电耗接近告警上限', value: '9.31', threshold: '9.60', status: 'resolved' },
    { code: 'ALM-20260921-0011', level: 'critical', source: '空压站网关', content: '网关离线超 24 小时', value: '—', threshold: '心跳 5min', status: 'resolved' }
  ];
  const rng = makeRng('alarms');
  return defs.map((d, i) => ({
    id: i + 1,
    code: d.code as string,
    level: d.level,
    source: d.source,
    content: d.content,
    value: (d.value as string) ?? '—',
    threshold: (d.threshold as string) ?? '—',
    status: d.status as AlarmRecord['status'],
    occurredAt: priorTime(12 + i * 47),
    ackedBy: d.status === 'active' ? '' : pick(NICKNAMES, rng),
    ackedAt: d.status === 'active' ? '' : priorTime(10 + i * 40)
  }));
}

/* ══════════════════════════════════════════════════════════════
   日志
   ══════════════════════════════════════════════════════════════ */

export function mockCollectLogs(): CollectLog[] {
  const results: CollectLog['result'][] = ['success', 'success', 'success', 'partial', 'success', 'failed'];
  const rng = makeRng('collect');
  return Array.from({ length: 60 }, (_, i) => {
    const result = results[i % results.length];
    const gateway = pick(mockGateways(), rng).name;
    return {
      id: i + 1,
      taskName: `整点数据采集 ${pad2(new Date().getHours())}${result === 'failed' ? '' : ':00'}`,
      gateway,
      pointCount: 60 + (i % 13),
      rows: result === 'failed' ? 0 : 120 + (i % 37),
      durationMs: 320 + ((i * 137) % 2400),
      result,
      message:
        result === 'failed'
          ? 'Modbus 读写超时（已重试 3 次）'
          : result === 'partial'
            ? '存在 2 个点位未上报，其余正常入库'
            : '全部点位入库成功',
      startedAt: priorTime(i * 30 + 2)
    };
  });
}

export function mockOperationLogs(): OperationLog[] {
  const modules = ['用户管理', '系统参数', '告警管理', '产线主数据', '能源报表', '计量点位'];
  const actions = ['新增', '修改', '删除', '导出', '启用', '停用'];
  const rng = makeRng('operation');
  return Array.from({ length: 80 }, (_, i) => ({
    id: i + 1,
    username: pick(PREFIX, rng),
    module: modules[i % modules.length],
    action: actions[(i * 3) % actions.length],
    target: `${modules[i % modules.length]}#${100 + i}`,
    ip: `10.20.31.${20 + (i % 60)}`,
    ua: i % 3 === 0 ? 'Chrome 131 / Windows 10' : i % 3 === 1 ? 'Edge 131 / Windows 11' : 'Firefox 133 / Ubuntu 24.04',
    result: i % 11 === 0 ? 'failed' : 'success',
    operatedAt: priorTime(i * 23 + 5)
  }));
}

export function mockLoginLogs(): LoginLog[] {
  const rng = makeRng('login');
  return Array.from({ length: 50 }, (_, i) => {
    const ok = i % 7 !== 0;
    return {
      id: i + 1,
      username: pick(PREFIX, rng),
      ip: `10.20.31.${20 + (i % 60)}`,
      location: '内网 · 华东生产基地',
      browser: i % 2 === 0 ? 'Chrome 131' : 'Edge 131',
      os: i % 2 === 0 ? 'Windows 10' : 'Windows 11',
      result: ok ? 'success' : 'failed',
      message: ok ? '登录成功' : '密码错误（连续失败 3 次，已锁定 5 分钟）',
      loginAt: priorTime(i * 41 + 3)
    };
  });
}

/* ══════════════════════════════════════════════════════════════
   报表与数据质量
   ══════════════════════════════════════════════════════════════ */

export function mockReports(): ReportTemplate[] {
  return [
    { id: 1, name: '车间能源日报', cycle: '日报', scope: '全车间', format: 'xlsx', enabled: true, lastGeneratedAt: priorTime(180) },
    { id: 2, name: '产线单位电耗周报', cycle: '周报', scope: 'A1—A6', format: 'xlsx', enabled: true, lastGeneratedAt: priorTime(2880) },
    { id: 3, name: '月度能源消耗分析', cycle: '月报', scope: '全车间', format: 'pdf', enabled: true, lastGeneratedAt: priorTime(14400) },
    { id: 4, name: '年度节能目标达成报告', cycle: '年报', scope: '全车间', format: 'pdf', enabled: false, lastGeneratedAt: priorTime(43200) },
    { id: 5, name: '原始小时明细导出', cycle: '日报', scope: '全车间', format: 'csv', enabled: true, lastGeneratedAt: priorTime(240) }
  ];
}

export function mockQuality(): DataQualityRow[] {
  return linesConfig.map((l, i) => {
    const expect = 24;
    const missing = i === 4 ? 3 : i === 5 ? 1 : 0;
    const received = expect - missing;
    const missHours = Array.from({ length: missing }, (_, k) => `${pad2(13 + k)}:00`).join('、');
    return {
      lineCode: l.id,
      lineName: l.name,
      expectPoints: expect,
      receivedPoints: received,
      missingPoints: missing,
      rate: round2((received / expect) * 100),
      missingHours: missHours || '—',
      lastAt: priorTime(missing ? 180 + i * 20 : 2 + i)
    };
  });
}

/* ══════════════════════════════════════════════════════════════
   首页看板
   ══════════════════════════════════════════════════════════════ */

export function mockAdminOverview(now: Date = new Date()): AdminOverview {
  const snap = buildSnapshot(now);
  const ws = snap.workshop;
  const alarms = mockAlarms();
  const gateways = mockGateways();
  const quality = mockQuality();
  const noise = makeRng(`ov|${now.getHours()}|${now.getDate()}`);

  return {
    kpi: {
      todayOutput: ws.today.output,
      todayKwh: ws.today.kwh,
      unitKwh: ws.today.unitKwh,
      activeAlarms: alarms.filter((a) => a.status === 'active').length,
      onlineGateways: gateways.filter((g) => g.status === 'online').length,
      totalGateways: gateways.length,
      dataQualityRate: round2(quality.reduce((a, q) => a + q.rate, 0) / quality.length)
    },
    hourly: ws.hours.map((h) => ({
      label: `${pad2(h.hour)}:00`,
      output: h.output ?? 0,
      kwh: h.kwh ?? 0,
      unitKwh: h.unitKwh
    })),
    daily30: ws.daily30,
    monthly12: ws.monthly12,
    lineUnitKwh: ws.lines.map((l) => ({
      lineCode: l.id,
      lineName: l.name,
      unitKwh: l.today.unitKwh ?? 0,
      output: l.today.output
    })),
    latestAlarms: alarms.slice(0, 6).map((a) => ({
      ...a,
      // 首页仅做展示，加一点确定性抖动避免与列表页完全一致
      occurredAt: a.occurredAt.replace(/:\d{2}$/, `:${pad2(Math.floor(noise() * 60))}`)
    }))
  };
}

/* ══════════════════════════════════════════════════════════════
   通用内存分页（mock 用）
   ══════════════════════════════════════════════════════════════ */

/** 对数组做关键词过滤 + 分页，模拟后端分页接口 */
export function paginate<T extends Record<string, unknown>>(
  rows: T[],
  query: PageQuery,
  keywordFields: (keyof T)[] = []
): PageResult<T> {
  const page = Math.max(1, Number(query.page) || 1);
  const pageSize = Math.max(1, Number(query.pageSize) || 10);
  const kw = (query.keyword ?? '').trim().toLowerCase();

  let filtered = rows;
  if (kw && keywordFields.length) {
    filtered = rows.filter((r) =>
      keywordFields.some((f) => String(r[f] ?? '').toLowerCase().includes(kw))
    );
  }
  const start = (page - 1) * pageSize;
  return {
    list: filtered.slice(start, start + pageSize),
    total: filtered.length,
    page,
    pageSize
  };
}
