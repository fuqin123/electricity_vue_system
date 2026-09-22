/**
 * 图标注册表
 * 显式导入 @element-plus/icons-vue 的图标并建立名称映射：
 *  · 用字符串在菜单配置里引用，避免直接耦合组件导入
 *  · 名称写错会在编译期报错，不会静默降级
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
  Key,
  Lightning,
  List,
  Menu as MenuIcon,
  Odometer,
  Operation,
  Refresh,
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
  DataLine,
  Document,
  Bell,
  Warning,
  Clock,
  SetUp,
  Cpu,
  DataAnalysis,
  Connection,
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

/** 按名称取图标组件；名称缺失或不存在时返回 null，由调用方决定降级表现 */
export function resolveIcon(name?: string): Component | null {
  return name ? (iconMap[name] ?? null) : null;
}
