<script setup lang="ts">
/**
 * PanelBox —— 统一面板容器：标题条 + 右侧备注 + 内容区
 * 通过 height 传入列高变量（如 var(--l1)）以维持栅格比例。
 */
defineProps<{
  title: string;
  note?: string;
  /** 额外说明，显示在标题右侧（小号弱化文本） */
  hint?: string;
  /** 面板高度，如 'var(--l1)'；不传则按 grow 自适应 */
  height?: string;
  /** 内容区是否补 12px 内边距（列表类默认不补） */
  bodyPad?: boolean;
  /** 标题行是否使用加高样式 */
  tall?: boolean;
  /** 内容区布局类 */
  bodyClass?: string;
}>();

defineSlots<{ default: () => unknown; note?: () => unknown }>();
</script>

<template>
  <section class="panel" :class="{ 'panel--grow': !height }" :style="height ? { height } : undefined">
    <div class="panel__head" :class="{ 'panel__head--tall': tall }">
      <div class="panel__title">
        <i />
        <h2>{{ title }}</h2>
        <em v-if="hint">{{ hint }}</em>
      </div>
      <slot name="note">
        <span v-if="note" class="panel__note">{{ note }}</span>
      </slot>
    </div>
    <div class="panel__body" :class="bodyClass">
      <slot />
    </div>
  </section>
</template>
