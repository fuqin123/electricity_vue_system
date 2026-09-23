/**
 * API SDK —— 唯一的「mock ↔ 真实接口」切换点
 *
 * 使用方式：
 *   const snap = await energyApi.getSnapshot();
 *
 * ── v1.2.1 起的按域切换 ─────────────────────────────────────
 * 后端不是一次性全做完的（系统管理类接口还在开发），所以不能只有一个总开关 ——
 * 一改成 'api' 就会让未实现的页面整片 404 白屏。现在的规则是：
 *
 *   · `dataConfig.mode = 'api'` → 除 `mockOnly` 里列出的域以外，都走真实接口；
 *   · `dataConfig.mockOnly`     → 后端尚未实现的域，继续用内置演示数据；
 *     后端实现后把它从数组里删掉即可，**不用改别处代码**。
 *
 * 已接真实后端：energy.*、admin.overview / lines / gateways / meters /
 *              quality / collect-logs
 * 仍走演示数据：admin.users / roles / menus / params / dicts / alarms /
 *              logs.operation / logs.login / reports
 *
 * ⚠ 大屏快照有个额外的安全网：后端不可达时**降级为演示数据，并把 `source`
 *   标成 'mock'、带上 `notice`** —— 界面必须显示这个提示。
 *   把演示数据当真实数据展示比白屏更危险（现场会据此判断生产情况）。
 */

import { dataConfig } from '../config';
import {
  mockAdminOverview,
  mockAlarms,
  mockCollectLogs,
  mockDicts,
  mockGateways,
  mockLines,
  mockLoginLogs,
  mockMeters,
  mockMenus,
  mockOperationLogs,
  mockParams,
  mockQuality,
  mockReports,
  mockRoles,
  mockUsers
} from '../mock/admin';
import { buildSnapshot } from '../mock/energy';
import type {
  AdminOverview,
  AlarmRecord,
  CollectLog,
  DataQualityRow,
  DictItem,
  EnergySnapshot,
  Gateway,
  LineData,
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

/** 登录令牌在 localStorage 的键名 */
export const TOKEN_KEY = 'eep_token';

/** 后端接口基地址：留空表示与前端同源（由 Nginx / IIS 反代到 Django） */
export const API_BASE = '';

/** 模拟网络延迟，让 loading 骨架可见 */
function delay<T>(data: T, ms = 220): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(data), ms));
}

/** 内存分页（mock 模式使用；真实模式交给后端） */
function page<T>(rows: T[], query: PageQuery): PageResult<T> {
  const p = Math.max(1, Number(query.page) || 1);
  const size = Math.max(1, Number(query.pageSize) || 10);
  const kw = String(query.keyword ?? '').trim().toLowerCase();
  const filtered = kw
    ? (rows as unknown as Record<string, unknown>[]).filter((r) =>
        Object.values(r).some((v) => String(v ?? '').toLowerCase().includes(kw))
      ) as unknown as T[]
    : rows;
  const start = (p - 1) * size;
  return { list: filtered.slice(start, start + size), total: filtered.length, page: p, pageSize: size };
}

/* ══════════════════════════════════════════════════════════════
   HTTP 客户端
   ══════════════════════════════════════════════════════════════ */

export interface RequestOptions extends RequestInit {
  /** 查询参数，自动拼接 */
  params?: Record<string, string | number | boolean | undefined | null>;
}

export async function request<T>(url: string, options: RequestOptions = {}): Promise<T> {
  const { params, ...init } = options;
  let fullUrl = API_BASE + url;
  if (params) {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.append(k, String(v));
    });
    const s = qs.toString();
    if (s) fullUrl += `${url.includes('?') ? '&' : '?'}${s}`;
  }

  const token = typeof localStorage !== 'undefined' ? localStorage.getItem(TOKEN_KEY) : null;
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...((init.headers as Record<string, string>) ?? {})
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';

  const res = await fetch(fullUrl, { ...init, headers });

  if (!res.ok) {
    // 后端出错时会带 {"detail": "..."} —— 把这句原样抛给界面，
    // 否则现场只看到「HTTP 409」，不知道到底是「编号重复」还是别的
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      /* 非 JSON 响应，保持 HTTP 状态码文案 */
    }
    throw new Error(detail);
  }

  const ct = res.headers.get('content-type') ?? '';
  if (!ct.includes('application/json')) return (await res.text()) as unknown as T;
  return (await res.json()) as T;
}

/**
 * 某个接口域是否走真实后端。
 * @param key 域标识，如 'admin.lines'
 */
function useApi(key: string): boolean {
  return dataConfig.mode === 'api' && !dataConfig.mockOnly.includes(key);
}

/* ══════════════════════════════════════════════════════════════
   大屏数据接口
   ══════════════════════════════════════════════════════════════ */

export const energyApi = {
  /**
   * 取一次全量快照（总览）
   * @param opts.date 指定查看日期（YYYY-MM-DD）；不传为今天。
   *   传历史日期时后端按「整天已结束」处理（24 小时全部计入），
   *   否则上午看昨天的数据，昨天下午的小时会被当成「还没到」而全部留空。
   */
  async getSnapshot(opts: { date?: string; now?: Date } = {}): Promise<EnergySnapshot> {
    const now = opts.now ?? new Date();
    if (dataConfig.mode === 'mock') {
      return delay({ ...buildSnapshot(now), source: 'mock' as const }, 120);
    }
    try {
      const res = await request<EnergySnapshot & { now: string }>(dataConfig.api.overview, {
        params: opts.date ? { date: opts.date } : undefined
      });
      if (!res?.workshop) throw new Error('接口返回结构不符合约定：缺少 workshop 字段');
      return { ...res, now: res.now ? new Date(res.now) : new Date(), source: 'api' as const };
    } catch (e) {
      // 后端不可达 → 降级为演示数据，但**必须让界面知道**，不能假装是真实数据
      const msg = (e as Error).message;
      console.error('[能源接口不可达] 已降级为演示数据：', e);
      return {
        ...buildSnapshot(now),
        source: 'mock' as const,
        notice: `后端接口不可达（${msg}），当前大屏显示的是演示数据，不可用于生产判断`
      };
    }
  },

  /** 取单条产线数据（后端已支持；保留独立方法便于将来拆分接口） */
  async getLine(lineId: string, opts: { date?: string; now?: Date } = {}): Promise<{ now: Date; line: LineData }> {
    const now = opts.now ?? new Date();
    if (dataConfig.mode === 'mock') {
      const snap = buildSnapshot(now);
      const line = snap.workshop.lines.find((l) => l.id === lineId.toUpperCase()) ?? snap.workshop.lines[0];
      return delay({ now: snap.now, line }, 120);
    }
    const url = dataConfig.api.line.replace('{lineId}', encodeURIComponent(lineId));
    const res = await request<LineData & { now?: string }>(url, {
      params: opts.date ? { date: opts.date } : undefined
    });
    return { line: res, now: res.now ? new Date(res.now) : new Date() };
  },

  /** 原始小时明细（用于导出 / 复核） */
  async getHourly(params: { date?: string; lineId?: string } = {}): Promise<Record<string, unknown>[]> {
    if (!useApi('energy.hourly')) return delay([] as Record<string, unknown>[], 80);
    const res = await request<{ items: Record<string, unknown>[] }>(dataConfig.api.hourly, {
      params: { date: params.date, line: params.lineId }
    });
    return res.items ?? [];
  },

  /**
   * 按时间段查询用量（大屏与后台共用 /api/energy/range，数字同源）
   *
   * @param granularity hour | day | month
   * @param scope       workshop | line | all
   */
  async getRange(params: {
    from: string;
    to: string;
    granularity?: 'hour' | 'day' | 'month';
    scope?: 'workshop' | 'line' | 'all';
    line?: string;
  }): Promise<UsageRangeResult> {
    if (!useApi('energy.range')) {
      const empty: UsageRangeResult = {
        from: params.from,
        to: params.to,
        granularity: params.granularity ?? 'day',
        scope: params.scope ?? 'workshop',
        labels: [],
        series: [],
        utility: { water: [], steam: [] },
        totals: { kwh: null, output: 0, waterT: null, steamT: null },
        productionAvailable: false,
        bucketCount: 0
      };
      return delay(empty, 120);
    }
    return request<UsageRangeResult>('/api/energy/range', {
      params: { ...params, granularity: params.granularity ?? 'day', scope: params.scope ?? 'workshop' }
    });
  },

  /** 小时用量明细（可导出 / 复核），支持时间范围与分页 */
  async getHourlyRange(params: {
    from: string;
    to: string;
    page?: number;
    pageSize?: number;
    line?: string;
    kind?: string;
  }): Promise<{ from: string; to: string; count: number; total: number; items: Record<string, unknown>[] }> {
    if (!useApi('energy.hourly')) {
      return delay({ from: params.from, to: params.to, count: 0, total: 0, items: [] }, 100);
    }
    return request(dataConfig.api.hourly, { params });
  },

  /** 日 / 月 / 年总计（后端 eep_period_summary，与后台同源） */
  async getPeriodSummary(
    params: { dim?: 'day' | 'month' | 'year'; scope?: 'line' | 'workshop'; line?: string; limit?: number } = {}
  ): Promise<Record<string, unknown>[]> {
    if (!useApi('energy.periodSummary')) return delay([] as Record<string, unknown>[], 80);
    const res = await request<{ items: Record<string, unknown>[] }>('/api/energy/period-summary', { params });
    return res.items ?? [];
  }
};

/* ══════════════════════════════════════════════════════════════
   后台管理数据接口
   ══════════════════════════════════════════════════════════════ */

export const adminApi = {
  /** 首页看板 */
  async getOverview(): Promise<AdminOverview> {
    if (!useApi('admin.overview')) return delay(mockAdminOverview(), 180);
    return request<AdminOverview>('/api/admin/overview');
  },

  /* ── 系统管理（后端未实现，仍用演示数据）── */
  async getUsers(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<SysUser>> {
    if (!useApi('admin.users')) return delay(page(mockUsers(), query));
    return request<PageResult<SysUser>>('/api/admin/users', { params: query as Record<string, string> });
  },
  async getRoles(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<SysRole>> {
    if (!useApi('admin.roles')) return delay(page(mockRoles(), query));
    return request<PageResult<SysRole>>('/api/admin/roles', { params: query as Record<string, string> });
  },
  async getMenus(): Promise<SysMenu[]> {
    if (!useApi('admin.menus')) return delay(mockMenus(), 120);
    return request<SysMenu[]>('/api/admin/menus');
  },
  async getParams(query: PageQuery = { page: 1, pageSize: 20 }): Promise<PageResult<SysParam>> {
    if (!useApi('admin.params')) return delay(page(mockParams(), query));
    return request<PageResult<SysParam>>('/api/admin/params', { params: query as Record<string, string> });
  },
  async getDicts(query: PageQuery = { page: 1, pageSize: 50 }): Promise<PageResult<DictItem>> {
    if (!useApi('admin.dicts')) return delay(page(mockDicts(), query));
    return request<PageResult<DictItem>>('/api/admin/dicts', { params: query as Record<string, string> });
  },

  /* ── 生产管理 ── */
  async getLines(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<LineMaster>> {
    if (!useApi('admin.lines')) return delay(page(mockLines(), query));
    return request<PageResult<LineMaster>>('/api/admin/lines', { params: query as Record<string, string> });
  },

  /* ── 设备与采集 ── */
  async getMeters(query: PageQuery = { page: 1, pageSize: 20 }): Promise<PageResult<MeterPoint>> {
    if (!useApi('admin.meters')) {
      // 演示模式下后端不在，筛选得自己实现 —— 否则筛选条件点了没反应
      let rows = mockMeters();
      const kindQ = String(query.kind ?? '');
      const catQ = String(query.category ?? '');
      const gwQ = String(query.gateway ?? '');
      const stQ = String(query.status ?? '');
      if (kindQ) rows = rows.filter((r) => r.kind === kindQ);
      if (catQ) rows = rows.filter((r) => r.category === catQ);
      if (gwQ) rows = rows.filter((r) => r.gatewayCode === gwQ);
      if (stQ) rows = rows.filter((r) => r.status === stQ);
      return delay(page(rows, query));
    }
    return request<PageResult<MeterPoint>>('/api/admin/meters', { params: query as Record<string, string> });
  },
  async getGateways(): Promise<Gateway[]> {
    if (!useApi('admin.gateways')) return delay(mockGateways(), 140);
    return request<Gateway[]>('/api/admin/gateways');
  },

  /** 新增 / 修改网关。id 为空表示新增。 */
  async saveGateway(payload: Partial<Gateway>, id?: number): Promise<Gateway> {
    if (!useApi('admin.gateways')) return delay({ ...(payload as Gateway), id: id ?? Date.now() }, 260);
    return request<Gateway>(id ? `/api/admin/gateways/${id}` : '/api/admin/gateways', {
      method: id ? 'PUT' : 'POST',
      body: JSON.stringify(payload)
    });
  },

  async deleteGateway(id: number): Promise<void> {
    if (!useApi('admin.gateways')) return delay(undefined, 200);
    await request(`/api/admin/gateways/${id}`, { method: 'DELETE' });
  },

  /** 网关连接测试：逐点试读，返回真实读数 */
  async testGateway(id: number): Promise<GatewayTestResult> {
    if (!useApi('admin.gateways')) {
      return delay({
        gateway: `GW-${id}`,
        reachable: true,
        detail: '演示模式：未连接真实网关',
        durationMs: 0,
        items: []
      });
    }
    return request<GatewayTestResult>(`/api/admin/gateways/${id}/test`, { method: 'POST' });
  },

  /** 新增 / 修改计量点 */
  async saveMeter(payload: Partial<MeterPoint>, id?: number): Promise<MeterPoint> {
    if (!useApi('admin.meters')) return delay({ ...(payload as MeterPoint), id: id ?? Date.now() }, 260);
    return request<MeterPoint>(id ? `/api/admin/meters/${id}` : '/api/admin/meters', {
      method: id ? 'PUT' : 'POST',
      body: JSON.stringify(payload)
    });
  },

  async deleteMeter(id: number): Promise<void> {
    if (!useApi('admin.meters')) return delay(undefined, 200);
    await request(`/api/admin/meters/${id}`, { method: 'DELETE' });
  },

  /** 试采一次：返回**真实读数**与原始报文，现场调参用 */
  async testMeter(id: number): Promise<MeterTestResult> {
    if (!useApi('admin.meters')) {
      return delay({
        ok: false,
        detail: '演示模式：未连接真实网关',
        meter: '',
        value: null,
        raw: '',
        error: ''
      });
    }
    return request<MeterTestResult>(`/api/admin/meters/${id}/test`, { method: 'POST' });
  },

  async getQuality(): Promise<DataQualityRow[]> {
    if (!useApi('admin.quality')) return delay(mockQuality(), 140);
    return request<DataQualityRow[]>('/api/admin/quality');
  },

  /* ── 告警（后端未实现，仍用演示数据）── */
  async getAlarms(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<AlarmRecord>> {
    if (!useApi('admin.alarms')) return delay(page(mockAlarms(), query));
    return request<PageResult<AlarmRecord>>('/api/admin/alarms', { params: query as Record<string, string> });
  },
  /** 确认告警（真实模式下由后端写入审计日志） */
  async ackAlarm(id: number): Promise<void> {
    if (!useApi('admin.alarms')) return delay(undefined, 260);
    await request(`/api/admin/alarms/${id}/ack`, { method: 'POST' });
  },

  /* ── 日志 ── */
  async getCollectLogs(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<CollectLog>> {
    if (!useApi('admin.collectLogs')) return delay(page(mockCollectLogs(), query));
    return request<PageResult<CollectLog>>('/api/admin/collect-logs', { params: query as Record<string, string> });
  },
  async getOperationLogs(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<OperationLog>> {
    if (!useApi('admin.logs.operation')) return delay(page(mockOperationLogs(), query));
    return request<PageResult<OperationLog>>('/api/admin/logs/operation', { params: query as Record<string, string> });
  },
  async getLoginLogs(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<LoginLog>> {
    if (!useApi('admin.logs.login')) return delay(page(mockLoginLogs(), query));
    return request<PageResult<LoginLog>>('/api/admin/logs/login', { params: query as Record<string, string> });
  },

  /* ── 报表（后端未实现）── */
  async getReports(): Promise<ReportTemplate[]> {
    if (!useApi('admin.reports')) return delay(mockReports(), 140);
    return request<ReportTemplate[]>('/api/admin/reports');
  }
};

/** 时间段查询结果 */
export interface UsageRangeSeries {
  key: string;
  name: string;
  unit: string;
  values: (number | null)[];
}

export interface UsageRangeResult {
  from: string;
  to: string;
  granularity: 'hour' | 'day' | 'month';
  scope: string;
  line?: string | null;
  labels: string[];
  series: UsageRangeSeries[];
  /** 每个时间桶的数据质量（车间序列才有，用于标出缺数段） */
  quality?: string[];
  utility: { water: (number | null)[]; steam: (number | null)[] };
  totals: { kwh: number | null; output: number; waterT: number | null; steamT: number | null };
  /** 产量侧是否已接入；false 时 totals.output 恒为 0，界面必须标注 */
  productionAvailable: boolean;
  bucketCount: number;
  cadence?: string;
}

/** 单个计量点的试采结果 */
export interface MeterTestResult {
  meter?: string;
  name?: string;
  ok: boolean;
  value: number | null;
  raw: string;
  error: string;
  unit?: string;
  durationMs?: number;
  /** 后端整理好的一句话说明，直接展示即可 */
  detail: string;
  extra?: Record<string, unknown>;
}

/** 网关连接测试结果 */
export interface GatewayTestResult {
  gateway: string;
  endpoint?: string;
  reachable: boolean;
  detail: string;
  durationMs: number;
  items: MeterTestResult[];
}

/* ══════════════════════════════════════════════════════════════
   认证
   ══════════════════════════════════════════════════════════════ */

export interface LoginPayload {
  username: string;
  password: string;
  captcha?: string;
  remember?: boolean;
}

export interface LoginResult {
  token: string;
  user: { username: string; nickname: string; roleName: string; avatarText: string };
}

/**
 * 登录
 * ⚠ 正式环境必须走统一身份认证（SSO），不允许独立账号体系。
 *   此处演示校验仅用于前端框架联调，任何非空密码均视为通过。
 */
export async function login(payload: LoginPayload): Promise<LoginResult> {
  if (!useApi('auth')) {
    if (!payload.username || !payload.password) throw new Error('请输入用户名与密码');
    if (payload.username === 'locked') throw new Error('账号已锁定，请联系管理员');
    return delay(
      {
        token: `demo-token-${Date.now()}`,
        user: {
          username: payload.username,
          nickname: payload.username === 'admin' ? '超级管理员' : payload.username,
          roleName: payload.username === 'admin' ? '超级管理员' : '能源管理员',
          avatarText: (payload.username[0] ?? 'U').toUpperCase()
        }
      },
      420
    );
  }
  return request<LoginResult>('/api/auth/login', { method: 'POST', body: JSON.stringify(payload) });
}

export async function logout(): Promise<void> {
  if (!useApi('auth')) return delay(undefined, 120);
  await request('/api/auth/logout', { method: 'POST' });
}
