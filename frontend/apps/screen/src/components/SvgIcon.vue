<script setup lang="ts">
/**
 * 内联 SVG 图标集 —— 不依赖图标字体，离线环境可用
 * 用法：<SvgIcon name="camera" />
 */
import { computed } from 'vue';

const props = defineProps<{
  name: string;
  /** 需要跟随父级文字颜色时置 true（默认 false，使用图标自带配色） */
  current?: boolean;
}>();

type IconDef = { viewBox: string; inner: string };

const stroke = (inner: string, color: string, w = 1.8) =>
  `<g stroke="${color}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round">${inner}</g>`;

const ICONS: Record<string, IconDef> = {
  /* 品牌标识 */
  logo: {
    viewBox: '0 0 28 28',
    inner:
      '<path d="M14 2.2L24.2 8.1V19.9L14 25.8L3.8 19.9V8.1L14 2.2Z" stroke="#00b0f4" stroke-width="1.6"/>' +
      '<path d="M9.6 18.4h8.8M9.6 14.8h8.8" stroke="#5865f2" stroke-width="1.6" stroke-linecap="round"/>' +
      '<path d="M14 6.4V11" stroke="#ec48bd" stroke-width="1.8" stroke-linecap="round"/>' +
      '<circle cx="14" cy="12.9" r="1.3" fill="#35ed7e"/>'
  },
  'arrow-left': { viewBox: '0 0 14 14', inner: stroke('<path d="M11.5 7H2.6M6.4 3.3L2.6 7l3.8 3.7"/>', 'currentColor', 1.7) },
  'arrow-up': { viewBox: '0 0 12 12', inner: stroke('<path d="M3 9L9 3M9 3H4.2M9 3v4.8"/>', '#35ed7e', 1.7) },
  'arrow-down': { viewBox: '0 0 12 12', inner: stroke('<path d="M3 3L9 9M9 9H4.2M9 9V4.2"/>', '#ec48bd', 1.7) },
  camera: {
    viewBox: '0 0 30 30',
    inner: stroke(
      '<path d="M4.5 25.5V10.8l6.6 4.2v-4.2l6.6 4.2V6.6l7.8 4.4v14.5H4.5Z"/>' +
        '<circle cx="10" cy="21" r="1.6"/><circle cx="18.5" cy="21" r="1.6"/>',
      '#5f6ba8',
      1.6
    )
  },
  play: { viewBox: '0 0 12 12', inner: '<path d="M3 2l7 4-7 4V2Z" fill="currentColor"/>' },
  admin: {
    viewBox: '0 0 14 14',
    inner: stroke('<circle cx="7" cy="5" r="2.4"/><path d="M2.4 12c0-2.4 2.1-3.9 4.6-3.9s4.6 1.5 4.6 3.9"/>', 'currentColor', 1.5)
  },
  refresh: {
    viewBox: '0 0 14 14',
    inner: stroke('<path d="M12 7a5 5 0 1 1-1.6-3.7M12 1.8v3.4H8.6"/>', 'currentColor', 1.5)
  }
};

const def = computed(() => ICONS[props.name] ?? { viewBox: '0 0 14 14', inner: '' });
</script>

<template>
  <svg
    :viewBox="def.viewBox"
    width="100%"
    height="100%"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    v-html="def.inner"
  />
</template>
