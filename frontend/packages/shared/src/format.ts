/**
 * 数值格式化与通用工具
 */

/** 千分位整数，如 1234567 → 1,234,567；空值返回 '--' */
export function int(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--';
  return Math.round(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/**
 * 电量显示口径：**保留 1 位小数，整数值不带尾随 .0**（v1.4.1，B-524）。
 *
 * 为什么单独开一个函数：大屏原来对 ≥20 kWh 一律取整（24.8 → 25），
 * 而后台表格一直是 1 位小数。两边逐格核对时每一行都差 0.x，
 * 现场的第一反应是「系统数字对不上」，于是真正的问题（缺数、结算延迟）
 * 反而被这种纯显示差异淹掉了。大屏与后台同源同口径是硬要求，
 * 所以电量类数字统一走这里，不再按量级切换精度。
 */
export function dec1(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--';
  const r = Math.round(v * 10) / 10;
  const text = Number.isInteger(r) ? String(r) : r.toFixed(1);
  return text.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/** 固定小数位，空值返回 '--' */
export function dec(v: number | null | undefined, d = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--';
  return Number(v).toFixed(d);
}

/** 瓶 → 万瓶 */
export function wan(v: number | null | undefined, d = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--';
  return (v / 10000).toFixed(d);
}

/** 大数自动降级：≥1e8 用「亿」，≥1e4 用「万」；万以下非整数保留 1 位小数 */
export function big(v: number | null | undefined, unit = ''): { v: string; u: string } {
  if (v === null || v === undefined || Number.isNaN(v)) return { v: '--', u: unit };
  if (Math.abs(v) >= 1e8) return { v: (v / 1e8).toFixed(2), u: '亿' + unit };
  if (Math.abs(v) >= 1e4) return { v: (v / 1e4).toFixed(2), u: '万' + unit };
  // 产量本来就是整数（走 int 的千分位），电量类带小数 —— 不能把 385.5 显示成 386
  return { v: Number.isInteger(v) ? int(v) : dec1(v), u: unit };
}

/** 增长率（%）；分母为 0 或空值时返回 null，避免出现 ∞ */
export function rise(cur: number | null | undefined, prev: number | null | undefined): number | null {
  if (!prev || cur === null || cur === undefined) return null;
  return ((cur - prev) / prev) * 100;
}

/** 补零到 2 位 */
export const pad2 = (n: number | string): string => String(n).padStart(2, '0');

/**
 * 小时数据质量 → 界面文案。
 *
 * ⚠ 已结束的整点只要数据不可信就必须写「异常」。以前前端没有 quality 字段，
 *   只能拿 `partial` 当「进行中」用，于是 8 点那个端点偏离整点的小时到了 10 点
 *   还显示「进行中」—— 现场会以为等一会儿就补上了，其实它永远不会再变（B-518）。
 */
export const HOUR_QUALITY_LABEL: Record<string, string> = {
  ok: '正常',
  future: '计划',
  missing: '异常·缺数',
  reset: '异常·换表',
  spike: '异常·尖峰',
  partial: '异常·部分缺数',
  // 登记了但从未出过可信值 —— 要人去接线 / 给 token，
  // 与「接入过却缺采」（missing，要查链路）是两种处理动作，不能混成一个文案
  unconnected: '未接入'
};

/** 数值保留 n 位小数（四舍五入），用于统一 mock 与接口口径 */
export const round2 = (v: number): number => Math.round(v * 100) / 100;

/** Date → YYYY-MM-DD */
export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** Date → YYYY-MM-DD HH:mm:ss */
export function isoDateTime(d: Date): string {
  return `${isoDate(d)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/** 时钟文本：YYYY-MM-DD HH:mm:ss */
export function clockText(d: Date): string {
  return `${isoDate(d)}  ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/** 班次文本：周几 · 白班/夜班 时段 */
export function shiftText(d: Date): string {
  const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()];
  const h = d.getHours();
  const isDay = h >= 8 && h < 20;
  return `${wd} · ${isDay ? '白班' : '夜班'} ${isDay ? '08:00—20:00' : '20:00—08:00'}`;
}

/** hex 转 rgba */
export function hexA(hex: string, a: number): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}

/** HTML 转义 */
export function escapeHtml(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string
  );
}

/**
 * 确定性随机（FNV-1a 哈希 + mulberry32）
 * 用「产线 + 日期 + 小时」做种子，保证同一时刻反复刷新数值不跳变。
 */
export function makeRng(seedStr: string): () => number {
  let h = 2166136261;
  for (let i = 0; i < seedStr.length; i++) {
    h ^= seedStr.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  let s = h >>> 0;
  return function () {
    s |= 0;
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 从数组按种子确定性取值 */
export function pick<T>(arr: readonly T[], rng: () => number): T {
  return arr[Math.floor(rng() * arr.length) % arr.length];
}

/** 深拷贝（结构化克隆，用于 mock 分页等场景避免引用污染） */
export function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}
