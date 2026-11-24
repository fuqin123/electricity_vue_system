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
 *              quality / collect-logs / params / readings / alarms（v1.5.4 告警引擎）
 * 仍走演示数据：admin.users / roles / menus / dicts /
 *              logs.operation / logs.login / reports
 * ⚠ 这个清单必须与下面 config.ts 的 dataConfig.mockOnly 一致 ——
 *   两处各说一套，改代码的人就会按错的那份办事。
 *
 * ⚠ 大屏快照有个额外的安全网：后端不可达时**降级为演示数据，并把 `source`
 *   标成 'mock'、带上 `notice`** —— 界面必须显示这个提示。
 *   把演示数据当真实数据展示比白屏更危险（现场会据此判断生产情况）。
 */

import { dataConfig, localVideoConfigPayload, type VideoConfigPayload } from '../config';
import {
  mockAdminOverview,
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
  EnergyPriceRow,
  ReportTemplate,
  StandardUnitOptions,
  StandardUnitRow,
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
   * @param from / to  精确到小时的起止（`YYYY-MM-DD HH`）
   * @param date       ★ 单日查询（`YYYY-MM-DD`），与 from/to 二选一；同时给时 date 优先。
   *                   后台「电/水/汽计量」选的是「哪一天」，用它比拼 from/to 更直白。
   * @param granularity hour | day | month
   * @param scope       workshop | line | all
   * @param line        当 scope=line 时的产线编码
   * @param kind        elec | water | steam（三级筛选第一维）
   * @param workshop    车间编码，如 WS-CARB
   * @param usageType   用能类型，如「生产线用电」
   * @param device      设备名
   * @param node        组织节点编码（产线 / 辅助单元 / 车间）
   * @param binding     ★ v1.7.4 绑定设备 ID（按该设备勾选的表求和）
   * @param bindingType ★ v1.7.4 绑定类型 ID（合并该类型下全部设备，去重）
   * @param meter       ★ v1.7.4 单表查询：计量点编码
   *
   * ⚠ usageType / device / node / binding / meter 任一给定即进入「焦点」通道，
   *   只返回一条序列 —— 用户已经在看具体设备，再叠一整套产线序列只会让图变成一锅粥。
   *   binding 优先于三级维度，meter 优先于设备合计。
   */
  async getRange(params: {
    from?: string;
    to?: string;
    date?: string;
    granularity?: 'hour' | 'day' | 'month';
    scope?: 'workshop' | 'line' | 'all';
    line?: string;
    kind?: 'elec' | 'water' | 'steam';
    workshop?: string;
    usageType?: string;
    device?: string;
    node?: string;
    binding?: number;
    bindingType?: number;
    meter?: string;
  }): Promise<UsageRangeResult> {
    if (!useApi('energy.range')) {
      const empty: UsageRangeResult = {
        from: params.from ?? params.date ?? '',
        to: params.to ?? params.date ?? '',
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
    // 后端参数名是下划线（usage_type / node），前端保持驼峰，只在这一层转换
    return request<UsageRangeResult>('/api/energy/range', {
      params: {
        from: params.from,
        to: params.to,
        date: params.date,
        granularity: params.granularity ?? 'day',
        scope: params.scope ?? 'workshop',
        line: params.line,
        kind: params.kind,
        workshop: params.workshop,
        usage_type: params.usageType,
        device: params.device,
        node: params.node,
        binding: params.binding,
        binding_type: params.bindingType,
        meter: params.meter
      }
    });
  },

  /**
   * 计量点分类树（车间 → 用能类型 → 设备）。
   *
   * 后台「电/水/汽计量」页的三级下拉就吃这棵树 —— 与 `getRange` 同源，
   * 保证「下拉里能选到的」和「查询能查到的」永远是同一批点位。
   *
   * @param kind elec | water | steam；不传则返回全部能源
   */
  async getMeterCategories(params: { kind?: 'elec' | 'water' | 'steam' } = {}): Promise<MeterCategoryResult> {
    if (!useApi('energy.meterCategories')) {
      // 后端未启用时给一棵空树（而不是编一棵假的）—— 界面会显示「暂无计量点」
      return delay({ tree: [], count: 0 } as MeterCategoryResult, 120);
    }
    return request<MeterCategoryResult>('/api/energy/meter-categories', {
      params: { kind: params.kind }
    });
  },

  /**
   * 数据中心主数据（按能源的累计序列 + 单箱变动费用）。
   * `from` / `to` 精确到小时，格式 `YYYY-MM-DD HH`。
   */
  async getDashboard(params: {
    from?: string;
    to?: string;
    granularity?: 'hour' | 'day';
    line?: string;
  } = {}): Promise<DashboardResult> {
    if (!useApi('energy.dashboard')) {
      const empty: DashboardResult = {
        from: params.from ?? '',
        to: params.to ?? '',
        granularity: params.granularity ?? 'hour',
        labels: [],
        bucketCount: 0,
        energies: {
          electric: { unit: 'kWh', values: [], cumulative: [], cumulativeCovered: [], outputCumulative: [], unitCumulative: [] },
          water: { unit: 't', values: [], cumulative: [], cumulativeCovered: [], outputCumulative: [], unitCumulative: [], perLineAvailable: false },
          steam: { unit: 't', values: [], cumulative: [], cumulativeCovered: [], outputCumulative: [], unitCumulative: [], perLineAvailable: false }
        },
        cost: {
          available: false,
          values: [],
          cumulative: [],
          cumulativeCovered: [],
          perCase: [],
          perCaseCumulative: [],
          standardPerCase: null,
          blockers: ['后端接口未启用，走演示数据']
        },
        productionAvailable: false,
        totals: { kwh: null, waterT: null, steamT: null, output: null, costYuan: null, perCaseCost: null }
      };
      return delay(empty, 120);
    }
    return request<DashboardResult>('/api/energy/dashboard', {
      params: { ...params, granularity: params.granularity ?? 'hour' }
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
  },

  /** 视频监控配置（v1.7.5，大屏启动时应用；不含 RTSP 账号信息） */
  async getVideoConfig(): Promise<VideoConfigPayload> {
    if (dataConfig.mode === 'mock') return delay(localVideoConfigPayload(), 80);
    try {
      return await request<VideoConfigPayload>('/api/energy/video-config');
    } catch (e) {
      // 网关配置读不到不该让大屏白屏：退回内置默认值，并在控制台留痕
      console.error('[视频配置接口不可达] 已退回内置默认值：', e);
      return localVideoConfigPayload();
    }
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
  /**
   * 修改系统参数（成本单价 / 箱规 / 标准变动费用等）。
   * `value` 传 null 表示「清空 = 未标定」，与传 0 完全不同含义。
   */
  async saveParam(id: number, payload: Partial<SysParam>): Promise<SysParam> {
    if (!useApi('admin.params')) return delay({ ...(payload as SysParam), id }, 220);
    return request<SysParam>(`/api/admin/params/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
  },

  /** 标准单耗配置：按产线 + 瓶型容量查询 */
  async getStandardUnits(
    query: { workshop?: string; line?: string; capacity?: string } = {}
  ): Promise<StandardUnitOptions> {
    if (!useApi('admin.standardUnits')) {
      return delay({ workshops: [], lines: [], energies: [], rows: [] } as StandardUnitOptions, 120);
    }
    return request<StandardUnitOptions>('/api/admin/standard-units', {
      params: query as Record<string, string>
    });
  },
  /** 新增/修改一组标准单耗（电/水/汽三列）；空串 = 未标定，0 保存但界面显示 - */
  async saveStandardUnit(payload: {
    line: string;
    bottleCapacity: string;
    values: Record<string, string | number | null>;
    remark?: string;
  }): Promise<{ saved: number; row: StandardUnitRow | null }> {
    if (!useApi('admin.standardUnits')) return delay({ saved: 0, row: null }, 220);
    return request<{ saved: number; row: StandardUnitRow | null }>('/api/admin/standard-units', {
      method: 'PUT',
      body: JSON.stringify(payload)
    });
  },
  /** 删除一组标准单耗（line + 瓶型容量） */
  async deleteStandardUnit(line: string, bottleCapacity: string): Promise<{ deleted: number }> {
    if (!useApi('admin.standardUnits')) return delay({ deleted: 0 }, 180);
    const qs = new URLSearchParams({ line, capacity: bottleCapacity }).toString();
    return request<{ deleted: number }>(`/api/admin/standard-units?${qs}`, { method: 'DELETE' });
  },
  /** 能源单价（能源配置管理 → 能源单价） */
  async getEnergyPrices(): Promise<{ items: EnergyPriceRow[] }> {
    if (!useApi('admin.energyPrices')) return delay({ items: [] }, 120);
    return request<{ items: EnergyPriceRow[] }>('/api/admin/energy-prices');
  },
  async saveEnergyPrices(
    items: { key: string; value: string | number | null }[]
  ): Promise<{ saved: number; items: EnergyPriceRow[] }> {
    if (!useApi('admin.energyPrices')) return delay({ saved: 0, items: [] }, 220);
    return request<{ saved: number; items: EnergyPriceRow[] }>('/api/admin/energy-prices', {
      method: 'PUT',
      body: JSON.stringify({ items })
    });
  },
  /** 视频监控配置（v1.7.5）：网关 IP / 端口 + 各产线 RTSP 源 */
  async getVideoConfig(): Promise<VideoConfigPayload> {
    if (!useApi('admin.videoConfig')) return delay(localVideoConfigPayload(), 80);
    return request<VideoConfigPayload>('/api/admin/video-config');
  },
  async saveVideoConfig(payload: VideoConfigPayload): Promise<VideoConfigPayload> {
    if (!useApi('admin.videoConfig')) return delay(localVideoConfigPayload(), 120);
    return request<VideoConfigPayload>('/api/admin/video-config', {
      method: 'PUT',
      body: JSON.stringify(payload)
    });
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

  /* ── 计量绑定树（v1.7.4）────────────────────────────────── */
  /** 左侧车间/类型/设备树 + 右侧计量点池 */
  async getBindingTree(): Promise<MeterBindingTreeResult> {
    if (!useApi('admin.meterBindings')) {
      return delay(
        {
          workshops: [],
          options: { energies: [], meters: [], workshops: [], usageTypes: [], deviceNames: [] }
        },
        120
      );
    }
    return request<MeterBindingTreeResult>('/api/admin/meter-bindings/tree');
  },

  async createBindingWorkshop(
    payload: MeterBindingWorkshopPayload
  ): Promise<{ workshop: MeterBindingWorkshopNode }> {
    return request<{ workshop: MeterBindingWorkshopNode }>('/api/admin/meter-binding-workshops', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },

  async updateBindingWorkshop(
    id: number,
    payload: MeterBindingWorkshopPayload
  ): Promise<{ workshop: MeterBindingWorkshopNode }> {
    return request<{ workshop: MeterBindingWorkshopNode }>(
      `/api/admin/meter-binding-workshops/${id}`,
      { method: 'PUT', body: JSON.stringify(payload) }
    );
  },

  async deleteBindingWorkshop(id: number): Promise<{ deleted: number }> {
    return request<{ deleted: number }>(`/api/admin/meter-binding-workshops/${id}`, {
      method: 'DELETE'
    });
  },

  async createBindingType(
    payload: MeterBindingTypePayload
  ): Promise<{ type: MeterBindingTypeNode }> {
    return request<{ type: MeterBindingTypeNode }>('/api/admin/meter-binding-types', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },

  async updateBindingType(
    id: number,
    payload: MeterBindingTypeUpdatePayload
  ): Promise<{ type: MeterBindingTypeNode }> {
    return request<{ type: MeterBindingTypeNode }>(`/api/admin/meter-binding-types/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload)
    });
  },

  async deleteBindingType(id: number): Promise<{ deleted: number }> {
    return request<{ deleted: number }>(`/api/admin/meter-binding-types/${id}`, {
      method: 'DELETE'
    });
  },

  async createBindingDevice(
    payload: MeterBindingDevicePayload
  ): Promise<{ device: MeterBindingDeviceNode }> {
    return request<{ device: MeterBindingDeviceNode }>('/api/admin/meter-binding-devices', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },

  async updateBindingDevice(
    id: number,
    payload: MeterBindingDeviceUpdatePayload
  ): Promise<{ device: MeterBindingDeviceNode }> {
    return request<{ device: MeterBindingDeviceNode }>(
      `/api/admin/meter-binding-devices/${id}`,
      { method: 'PUT', body: JSON.stringify(payload) }
    );
  },

  async deleteBindingDevice(id: number): Promise<{ deleted: number }> {
    return request<{ deleted: number }>(`/api/admin/meter-binding-devices/${id}`, {
      method: 'DELETE'
    });
  },

  /** 保存右侧勾选：替换该设备绑定的全部计量点（空数组 = 全部解绑） */
  async setBindingDeviceMeters(
    id: number,
    meterCodes: string[]
  ): Promise<{ device: MeterBindingDeviceNode; saved: number }> {
    return request<{ device: MeterBindingDeviceNode; saved: number }>(
      `/api/admin/meter-binding-devices/${id}/meters`,
      { method: 'PUT', body: JSON.stringify({ meterCodes }) }
    );
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

  /** 试采一次：返回**真实读数**与原始报文，现场调参用。⚠ 会更新该点位的最近读数 */
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

  /**
   * ★ 读取某个计量点的**当前值**（v1.4.4 新增）。
   *
   * 与 `testMeter` 的区别只有一条，但很关键：**这个接口一处都不落库** ——
   * 不写 `energy_raw`、不更新点位的 `last_value / last_at / last_ok`。
   * 现场排障时随手看一眼当前读数，不应该把「最近读数」刷成刚刚成功，
   * 否则断讯的表在列表里显示成在线，事后分不清是链路恢复了还是有人点过按钮。
   *
   * @param opts.timeoutMs  单次读写超时（留空用网关配置）；后端裁剪到 200—10000ms
   * @param opts.retryTimes 失败后的额外重试次数（留空用 collect.retry_times）；0—5
   */
  async readMeterCurrent(id: number, opts: { timeoutMs?: number; retryTimes?: number } = {}): Promise<MeterCurrentResult> {
    if (!useApi('admin.meters')) {
      return delay({
        meterId: id,
        meter: '',
        name: '',
        ok: false,
        value: null,
        raw: '',
        error: '演示模式：未连接真实网关',
        unit: '',
        at: '',
        durationMs: 0,
        attempts: 0,
        timeoutMs: opts.timeoutMs ?? 1500,
        retryTimes: opts.retryTimes ?? 1,
        persisted: false
      });
    }
    return request<MeterCurrentResult>(`/api/admin/meters/${id}/current`, {
      method: 'POST',
      body: JSON.stringify(opts)
    });
  },

  async getQuality(): Promise<DataQualityRow[]> {
    if (!useApi('admin.quality')) return delay(mockQuality(), 140);
    return request<DataQualityRow[]>('/api/admin/quality');
  },

  /* ── 告警（读后端告警表，不再有演示数据）── */
  async getAlarms(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<AlarmRecord>> {
    // 演示模式给空列表而不是编几条假告警：告警的含义是「系统检测到这里有问题」，
    // 假的告警会让人以为问题正在被监控，比没有告警更糟。
    if (!useApi('admin.alarms')) return delay({ list: [], total: 0, page: 1, pageSize: 10 }, 160);
    return request<PageResult<AlarmRecord>>('/api/admin/alarms', { params: query as Record<string, string> });
  },
  /** 确认告警（写 acked_by / acked_at；操作审计日志尚未实现，不假装写了） */
  async ackAlarm(id: number): Promise<void> {
    if (!useApi('admin.alarms')) return delay(undefined, 260);
    await request(`/api/admin/alarms/${id}/ack`, { method: 'POST' });
  },

  /* ── 日志 ── */
  async getCollectLogs(query: PageQuery = { page: 1, pageSize: 10 }): Promise<PageResult<CollectLog>> {
    if (!useApi('admin.collectLogs')) return delay(page(mockCollectLogs(), query));
    return request<PageResult<CollectLog>>('/api/admin/collect-logs', { params: query as Record<string, string> });
  },
  /**
   * 仪表读数查询：按时间（精确到小时）/ 网关 / 仪表筛 `energy_raw` 的**原始累计读数**。
   *
   * 与 getHourlyRange 的分工：那边是差分后的**用量**，这边是读数本身 ——
   * 排查「倍率对不对、表有没有读歪」必须看原始累计值与报文，用量表里已经加工掉了。
   */
  async getReadings(query: ReadingQuery = {}): Promise<ReadingPage> {
    const empty: ReadingPage = {
      list: [],
      total: 0,
      page: query.page ?? 1,
      pageSize: query.pageSize ?? 50,
      from: query.from ?? '',
      to: query.to ?? ''
    };
    if (!useApi('admin.readings')) return delay(empty, 140);
    return request<ReadingPage>('/api/admin/readings', { params: query as unknown as Record<string, string> });
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

/**
 * 计量点分类树 —— 「车间 → 用能类型 → 设备」三级。
 *
 * 为什么不用「线别」那一套：现场接口表里辅机 / 瓶胚 / 调配的「线别」列写的是
 * 设备组合名（如「4号高压机+3号吹瓶冰水机」），根本不是一条产线。
 * 要用「线别」硬塞，这些表就会挂到一条不存在的线上，或者干脆漏掉。
 * 三级树是唯一能如实表达它们的结构。
 */
export interface MeterCategoryDevice {
  /** 计量点编码，如 E-A1-01 */
  code: string;
  /** 计量点名称，如「一线生产线电表」 */
  name: string;
  /** 设备名（分类用），如「生产线」 */
  deviceName: string;
  /** 所属组织节点编码（产线 W103 / 辅助单元 AUX-CARB-AIR / 车间 WS-CARB） */
  nodeCode: string;
  nodeName: string;
  /** line = 产线，aux = 辅助计量单元，workshop = 车间（总表） */
  nodeType: string;
  lineCode: string;
  /** 库内能源编码：ELECTRIC / WATER / STEAM */
  kind: string;
  /** 中文能源名：电 / 水 / 汽 */
  kindLabel: string;
  unit: string;
  enabled: boolean;
  gateway: string;
  /** 「怎么读到的」摘要：IP:端口 / 从站 N / 寄存器 M×2 */
  address: string;
}

/**
 * 绑定设备（v1.7.4）—— 一组计量点组成一个可查询的「设备」。
 *
 * 与 `MeterCategoryDevice` 的区别：后者是一块**物理表**，前者是绑定页
 * 左侧配置的查询口径。查询时传 `binding`（设备 ID），后端按这组表求和。
 */
export interface MeterCategoryBinding {
  id: number;
  /** 设备名称，如「一线 / 冰水 / 高压」 */
  name: string;
  deviceName: string;
  usageType: string;
  energy: 'elec' | 'water' | 'steam';
  unit: string;
  meterCount: number;
  meterCodes: string[];
  meterNames: string[];
  /** 该绑定的显示顺序（小在前） */
  sort: number;
  /** 恒为 true；保留字段用于前端区分「绑定」与「物理表」 */
  bound: true;
}

export interface MeterCategoryType {
  /** 绑定树模式下的类型 ID；原始模式为 null */
  id: number | null;
  /** 用能类型，如「生产线用电」「辅机用电」 */
  usageType: string;
  energy?: 'elec' | 'water' | 'steam';
  energyName?: string;
  unit?: string;
  /**
   * true = 该能源已切到绑定树模式：第三级只展示绑定设备/单块仪表，
   * 不再按物理表的 node/device_name 生成「节点/设备」分组。
   */
  bindingDriven?: boolean;
  /** 绑定树模式下的设备数（原始模式为 0） */
  deviceCount?: number;
  /** 已配置的绑定设备（可为空） */
  bindings: MeterCategoryBinding[];
  devices: MeterCategoryDevice[];
}

export interface MeterCategoryWorkshop {
  workshop: string;
  /** 绑定树车间为 BWS-<id>，原始模式为 production_line.line_code */
  workshopCode: string;
  /** true = 该车间来自绑定树 */
  bindingDriven?: boolean;
  types: MeterCategoryType[];
}

export interface MeterCategoryResult {
  tree: MeterCategoryWorkshop[];
  /** 树里的物理计量点总数 */
  count: number;
  /** 树里的绑定设备数 */
  bindingCount?: number;
}

/* ══════════════════════════════════════════════════════════════
   计量绑定（后台配置，v1.7.4）
   ══════════════════════════════════════════════════════════════ */

export type MeterBindingEnergy = 'elec' | 'water' | 'steam';

/** 绑定页可选的一块计量点（带车间 / 节点 / 分类，供表单分组与过滤） */
export interface MeterBindingMeter {
  id: number;
  code: string;
  name: string;
  energy: MeterBindingEnergy;
  energyName: string;
  unit: string;
  workshopCode: string;
  workshopName: string;
  nodeCode: string;
  nodeName: string;
  nodeType: 'line' | 'aux' | 'workshop' | string;
  usageType: string;
  deviceName: string;
  enabled: boolean;
}

/** 绑定树 · 设备节点（左侧第三级；右侧勾选的计量点挂在这里） */
export interface MeterBindingDeviceNode {
  id: number;
  name: string;
  deviceName: string;
  sort: number;
  typeId: number;
  typeName: string;
  workshopId: number;
  workshopName: string;
  energy: MeterBindingEnergy;
  energyName: string;
  unit: string;
  meterCodes: string[];
  meterCount: number;
  meters: MeterBindingMeter[];
}

/** 绑定树 · 类型节点（第二级） */
export interface MeterBindingTypeNode {
  id: number;
  name: string;
  usageType: string;
  energy: MeterBindingEnergy;
  energyName: string;
  unit: string;
  sort: number;
  devices: MeterBindingDeviceNode[];
  meterCount: number;
}

/** 绑定树 · 车间节点（第一级） */
export interface MeterBindingWorkshopNode {
  id: number;
  name: string;
  sort: number;
  types: MeterBindingTypeNode[];
  typeCount: number;
  meterCount: number;
}

export interface MeterBindingOptions {
  /** 真实组织车间（仅用于新建绑定车间时的名称建议） */
  workshops: { code: string; name: string }[];
  energies: { code: MeterBindingEnergy; name: string; unit: string }[];
  /** 现有计量台账里的用能类型 / 设备名，仅作建议词 */
  usageTypes: string[];
  deviceNames: string[];
  meters: MeterBindingMeter[];
}

export interface MeterBindingTreeResult {
  workshops: MeterBindingWorkshopNode[];
  options: MeterBindingOptions;
}

export interface MeterBindingWorkshopPayload {
  name: string;
  sort?: number;
}

export interface MeterBindingTypePayload {
  workshopId: number;
  energy: MeterBindingEnergy;
  name: string;
  sort?: number;
}

export interface MeterBindingTypeUpdatePayload {
  name?: string;
  sort?: number;
}

export interface MeterBindingDevicePayload {
  typeId: number;
  name: string;
  sort?: number;
}

export interface MeterBindingDeviceUpdatePayload {
  name?: string;
  sort?: number;
}

export interface UsageRangeResult {
  from: string;
  to: string;
  granularity: 'hour' | 'day' | 'month';
  scope: string;
  line?: string | null;
  /** 本次查询命中的绑定设备 ID（v1.7.4） */
  binding?: number;
  /** 本次查询命中的绑定类型 ID（v1.7.4，「全部设备」） */
  bindingType?: number;
  /** 本次查询命中的单表编码（v1.7.4） */
  meter?: string;
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
  /** 本次查询命中的计量点数（焦点通道用于显示「本范围 N 个点位」） */
  meterCount?: number;
  /**
   * 计量点覆盖度 —— 解释「这一段的数是从多少块表来的」。
   *
   * 两类「没数」必须分开，因为现场的处理动作完全不同：
   *   · missing      已接入、本区间却没出数 → 真故障，要查链路 / 看告警
   *   · unconnected  登记了但从未出过可信值 → 还没接线 / 没给 token
   *
   * ⚠ 口径变更：unconnected 的表**不再参与合计**（旧口径下一块没接的表会把
   *   整组永久否决，混类型时连电和汽一起抹空）。它们改由告警引擎报「未接入」。
   *   所以合计为 `--` 现在只可能是 missing 造成的，不再可能是 unconnected。
   */
  coverage?: {
    total: number;
    connected: number;
    covered: number;
    missing: string[];
    unconnected: string[];
  };
  /** 服务器认为的「参考日」（YYYY-MM-DD），用于跨零点校准日期控件 */
  serverDate?: string;
  /** 实际生效的区间端点（精确到小时文本），比 from/to 更准 */
  fromHour?: string;
  toHour?: string;
}

/**
 * 数据中心（后台首页）主数据 —— 按「电 / 水 / 汽」三张累计趋势图 + 单箱变动费用主图。
 * 与 /api/energy/range 同源同口径，三处数字必然一致。
 */
export interface DashboardEnergyBlock {
  /** 计量单位：kWh / t */
  unit: string;
  /** 每桶用量（缺数为 null，不是 0） */
  values: (number | null)[];
  /** 逐桶累计用量 */
  cumulative: (number | null)[];
  /** 累计到该桶时吃进了几格可信数据 —— 与 bucketCount 一起显示「已覆盖 N/M」 */
  cumulativeCovered: number[];
  /** 累计产量（瓶）；产量未接入时整段 null */
  outputCumulative: (number | null)[];
  /** 累计单耗（用量 ÷ 千瓶）；产量缺 → null */
  unitCumulative: (number | null)[];
  /** 该能源是否支持按线拆分（水/汽只有车间总表 → false） */
  perLineAvailable?: boolean;
}

export interface DashboardCostBlock {
  /** 算料是否齐全（单价 + 箱规 + 产量）。false 时下面几条序列都是 null */
  available: boolean;
  values: (number | null)[];
  cumulative: (number | null)[];
  cumulativeCovered: number[];
  perCase: (number | null)[];
  perCaseCumulative: (number | null)[];
  /** 每月标准变动费用（元/箱）。null = 未标定 → 不画基准虚线 */
  standardPerCase: number | null;
  /** 算不出费用时的原因，界面原样显示，别让人以为系统坏了 */
  blockers: string[];
}

export interface DashboardResult {
  from: string;
  to: string;
  granularity: 'hour' | 'day';
  line?: string | null;
  labels: string[];
  bucketCount: number;
  energies: { electric: DashboardEnergyBlock; water: DashboardEnergyBlock; steam: DashboardEnergyBlock };
  cost: DashboardCostBlock;
  productionAvailable: boolean;
  totals: {
    kwh: number | null;
    waterT: number | null;
    steamT: number | null;
    output: number | null;
    costYuan: number | null;
    perCaseCost: number | null;
  };
  cadence?: string;
}

/** 仪表原始读数（energy_raw）—— 累计值本身，不是用量 */
export interface MeterReading {
  id: number;
  collectTime: string;
  meter: string;
  meterId: number;
  meterName: string;
  gateway: string;
  gatewayId: number;
  line: string;
  lineName: string;
  energy: string;
  energyName: string;
  /** 累计读数；采集失败为 null（不是 0 —— 0 会被差值当成真实读数） */
  reading: number | null;
  unit: string;
  ok: boolean;
  raw: string;
  error: string;
  /** 该读数作为终点结算出的上一小时差值；无对应小时行时为 null */
  hourUsage: number | null;
  /** 对应小时行的质量（ok/partial/spike/reset/missing）；未匹配为空串 */
  hourQuality: string;
  /** ok / pending / not_boundary / missing / reset / spike / partial */
  hourStatus: string;
  hourStartAt: string;
  hourEndAt: string;
  hourNote: string;
}

export interface ReadingQuery {
  from?: string;
  to?: string;
  gateway?: number | string;
  meter?: number | string;
  line?: string;
  energy?: string;
  ok?: 0 | 1;
  page?: number;
  pageSize?: number;
}

export interface ReadingPage {
  list: MeterReading[];
  total: number;
  page: number;
  pageSize: number;
  from: string;
  to: string;
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

/**
 * 「读取当前值」结果（`POST /api/admin/meters/{id}/current`）。
 *
 * ⚠ `persisted` 恒为 `false`：本次读取不写数据库、不写日志、不更新点位最近读数。
 *   界面必须把这一点显式写给使用者看 —— 这是它与「试采」唯一的区别，
 *   也是最容易被误用的一条（随手一点就把断讯表的 `last_ok` 刷成在线）。
 */
export interface MeterCurrentResult {
  meterId: number;
  meter: string;
  name: string;
  /** 能源类型中文名，如「电」 */
  category?: string;
  lineCode?: string;
  gatewayCode?: string;
  gatewayName?: string;
  protocol?: string;
  /** 怎么读到的：地址 / 从站 / 寄存器（或接口地址） */
  address?: string;
  unit: string;
  /** 倍率（原始寄存器 × 倍率 = 读数） */
  factor?: number;
  /** 该点位是否处于启用状态（停用的表也允许读取，便于排障） */
  enabled?: boolean;
  ok: boolean;
  /** 当前累计读数；读取失败为 null（**不是 0**） */
  value: number | null;
  raw: string;
  error: string;
  /** API 类网关的额外字段（瞬时流量 / 温度 / 压力等） */
  extra?: Record<string, unknown>;
  /** 本次实际尝试了几次（>1 说明靠重试拿到了值） */
  attempts: number;
  /** 服务端读取时刻 `YYYY-MM-DD HH:mm:ss` */
  at: string;
  durationMs: number;
  /** 本次生效的单次读写超时 */
  timeoutMs: number;
  /** 本次生效的额外重试次数（总尝试 = retryTimes + 1） */
  retryTimes: number;
  /** 恒为 false：本次读取不留痕 */
  persisted: boolean;
  note?: string;
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
