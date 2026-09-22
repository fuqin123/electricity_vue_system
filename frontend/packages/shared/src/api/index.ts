/**
 * API SDK —— 唯一的「mock ↔ 真实接口」切换点
 *
 * 使用方式：
 *   const snap = await energyApi.getSnapshot();
 *
 * 切到真实接口：把 config.ts 中 dataConfig.mode 改为 'api'，
 * 并让后端返回与 mock 同构的结构（见 types.ts 中 EnergySnapshot / AdminOverview）。
 * 页面与图表的渲染代码完全不需要改动。
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
   HTTP 客户端（真实接口模式使用）
   ══════════════════════════════════════════════════════════════ */

export interface RequestOptions extends RequestInit {
  /** 查询参数，自动拼接 */
  params?: Record<string, string | number | boolean | undefined | null>;
}

export async function request<T>(url: string, options: RequestOptions = {}): Promise<T> {
  const { params, ...init } = options;
  let fullUrl = url;
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
  if (!res.ok) throw new Error(`HTTP ${res.status} ${fullUrl}`);
  const ct = res.headers.get('content-type') ?? '';
  if (!ct.includes('application/json')) return (await res.text()) as unknown as T;
  return (await res.json()) as T;
}

const isMock = (): boolean => dataConfig.mode === 'mock';

/* ══════════════════════════════════════════════════════════════
   大屏数据接口
   ══════════════════════════════════════════════════════════════ */

export const energyApi = {
  /** 取一次全量快照（总览） */
  async getSnapshot(now: Date = new Date()): Promise<EnergySnapshot> {
    if (isMock()) return delay(buildSnapshot(now), 120);
    const res = await request<EnergySnapshot & { now: string }>(dataConfig.api.overview);
    if (!res?.workshop) throw new Error('接口返回结构不符合约定：缺少 workshop 字段');
    return { ...res, now: res.now ? new Date(res.now) : new Date() };
  },

  /**
   * 取单条产线数据
   * 当前实现从总览快照中取线数据；若后端拆成独立接口，
   * 在此处补一次 request 调用即可，调用方无需改动。
   */
  async getLine(lineId: string, now: Date = new Date()): Promise<{ now: Date; line: LineData }> {
    if (isMock()) {
      const snap = buildSnapshot(now);
      const line = snap.workshop.lines.find((l) => l.id === lineId.toUpperCase()) ?? snap.workshop.lines[0];
      return delay({ now: snap.now, line }, 120);
    }
    const url = dataConfig.api.line.replace('{lineId}', encodeURIComponent(lineId));
    const res = await request<{ now: string; line: LineData }>(url);
    return { line: res.line, now: res.now ? new Date(res.now) : new Date() };
  },

  /** 原始小时明细（用于导出） */
  async getHourly(params: { date?: string; lineId?: string } = {}): Promise<Record<string, unknown>[]> {
    if (isMock()) return delay([], 80);
    return request<Record<string, unknown>[]>(dataConfig.api.hourly, { params });
  }
};

/* ══════════════════════════════════════════════════════════════
   后台管理数据接口
   ══════════════════════════════════════════════════════════════ */

export const adminApi = {
  /** 首页看板 */
  async getOverview(): Promise<AdminOverview> {
    if (isMock()) return delay(mockAdminOverview(), 180);
    return request<AdminOverview>('/api/admin/overview');
  },

  /* ── 系统管理 ── */
  async getUsers(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<SysUser>> {
    if (isMock()) return delay(page(mockUsers(), query));
    return request<PageResult<SysUser>>('/api/admin/users', { params: query as Record<string, string> });
  },
  async getRoles(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<SysRole>> {
    if (isMock()) return delay(page(mockRoles(), query));
    return request<PageResult<SysRole>>('/api/admin/roles', { params: query as Record<string, string> });
  },
  async getMenus(): Promise<SysMenu[]> {
    if (isMock()) return delay(mockMenus(), 120);
    return request<SysMenu[]>('/api/admin/menus');
  },
  async getParams(query: PageQuery = { page: 1, pageSize: 20 }): Promise<PageResult<SysParam>> {
    if (isMock()) return delay(page(mockParams(), query));
    return request<PageResult<SysParam>>('/api/admin/params', { params: query as Record<string, string> });
  },
  async getDicts(query: PageQuery = { page: 1, pageSize: 50 }): Promise<PageResult<DictItem>> {
    if (isMock()) return delay(page(mockDicts(), query));
    return request<PageResult<DictItem>>('/api/admin/dicts', { params: query as Record<string, string> });
  },

  /* ── 生产管理 ── */
  async getLines(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<LineMaster>> {
    if (isMock()) return delay(page(mockLines(), query));
    return request<PageResult<LineMaster>>('/api/admin/lines', { params: query as Record<string, string> });
  },

  /* ── 设备与采集 ── */
  async getMeters(query: PageQuery = { page: 1, pageSize: 20 }): Promise<PageResult<MeterPoint>> {
    if (isMock()) return delay(page(mockMeters(), query));
    return request<PageResult<MeterPoint>>('/api/admin/meters', { params: query as Record<string, string> });
  },
  async getGateways(): Promise<Gateway[]> {
    if (isMock()) return delay(mockGateways(), 140);
    return request<Gateway[]>('/api/admin/gateways');
  },
  async getQuality(): Promise<import('../types').DataQualityRow[]> {
    if (isMock()) return delay(mockQuality(), 140);
    return request<import('../types').DataQualityRow[]>('/api/admin/quality');
  },

  /* ── 告警 ── */
  async getAlarms(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<AlarmRecord>> {
    if (isMock()) return delay(page(mockAlarms(), query));
    return request<PageResult<AlarmRecord>>('/api/admin/alarms', { params: query as Record<string, string> });
  },
  /** 确认告警（真实模式下由后端写入审计日志） */
  async ackAlarm(id: number): Promise<void> {
    if (isMock()) return delay(undefined, 260);
    await request(`/api/admin/alarms/${id}/ack`, { method: 'POST' });
  },

  /* ── 日志 ── */
  async getCollectLogs(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<CollectLog>> {
    if (isMock()) return delay(page(mockCollectLogs(), query));
    return request<PageResult<CollectLog>>('/api/admin/logs/collect', { params: query as Record<string, string> });
  },
  async getOperationLogs(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<OperationLog>> {
    if (isMock()) return delay(page(mockOperationLogs(), query));
    return request<PageResult<OperationLog>>('/api/admin/logs/operation', { params: query as Record<string, string> });
  },
  async getLoginLogs(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<LoginLog>> {
    if (isMock()) return delay(page(mockLoginLogs(), query));
    return request<PageResult<LoginLog>>('/api/admin/logs/login', { params: query as Record<string, string> });
  },

  /* ── 报表 ── */
  async getReports(): Promise<ReportTemplate[]> {
    if (isMock()) return delay(mockReports(), 140);
    return request<ReportTemplate[]>('/api/admin/reports');
  }
};

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
 *   此处 mock 仅用于前端框架联调，任何非空密码均视为通过。
 */
export async function login(payload: LoginPayload): Promise<LoginResult> {
  if (isMock()) {
    if (!payload.username || !payload.password) throw new Error('请输入用户名与密码');
    if (payload.username === 'locked') throw new Error('账号已锁定，请联系管理员');
    return delay(
      {
        token: `mock-token-${Date.now()}`,
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
  if (isMock()) return delay(undefined, 120);
  await request('/api/auth/logout', { method: 'POST' });
}
