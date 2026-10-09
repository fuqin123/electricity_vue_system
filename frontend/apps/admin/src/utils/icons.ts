/**
 * 图标注册表
 * 显式导入 @element-plus/icons-vue 的图标并建立名称映射：
 *  · 用字符串在菜单配置里引用，避免直接耦合组件导入
 *  · 本文件导入的名字写错会在编译期报错
 *
 * ⚠ 但「菜单里写了名字、这里忘了登记」是**静默降级**的 —— 那一行只是少个图标，
 *   侧栏看起来就是「同样的缩进里只有它没对齐」。历史事故：v1.4.4 之前
 *   `用量查询`(Search) 与 `仪表读数查询`(Histogram) 两个名字没登记，正是这个症状。
 *   新增菜单项时务必把图标同时补进本文件。
 */
import {
  Bell,
  Box,
  Camera,
  CircleCheck,
  Clock,
  Collection,
  Connection,
  Cpu,
  DataAnalysis,
  DataLine,
  Document,
  Grid,
  Histogram,
  Key,
  Lightning,
  List,
  Menu as MenuIcon,
  Odometer,
  Operation,
  Refresh,
  Search,
  SetUp,
  Setting,
  Tickets,
  Tools,
  User,
  UserFilled,
  VideoCamera,
  Warning
} from '@element-plus/icons-vue';
import type { Component } from 'vue';

export const iconMap: Record<string, Component> = {
  Odometer,
  Grid,
  List,
  Box,
  Lightning,
  Search,
  DataLine,
  Document,
  Bell,
  Warning,
  Clock,
  SetUp,
  Cpu,
  DataAnalysis,
  Connection,
  Histogram,
  CircleCheck,
  VideoCamera,
  Camera,
  Tickets,
  Refresh,
  Operation,
  Key,
  Setting,
  User,
  UserFilled,
  Menu: MenuIcon,
  Tools,
  Collection
};

/**
 * 按名称取图标组件；名称缺失或不存在时返回 null，由调用方决定降级表现。
 *
 * ⚠ 开发环境对「名字没登记」发一次 console.warn —— 这个降级是静默的（只少个图标），
 *   不主动报出来就只能靠肉眼看侧栏对齐，很容易漏（见文件头的历史事故）。
 */
const warnedIcons = new Set<string>();

export function resolveIcon(name?: string): Component | null {
  if (!name) return null;
  const icon = iconMap[name] ?? null;
  if (!icon && import.meta.env.DEV && !warnedIcons.has(name)) {
    warnedIcons.add(name);
    console.warn(`[icons] 图标未在 utils/icons.ts 登记：${name} —— 该处会少显示一个图标`);
  }
  return icon;
}
