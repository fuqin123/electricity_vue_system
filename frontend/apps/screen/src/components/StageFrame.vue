<script setup lang="ts">
/**
 * StageFrame —— 1920×1080 固定舞台 + 氛围背景 + 屏幕容器
 * 所有大屏视图都套在这一层里，保证构图在多分辨率下完全一致。
 */
import { computed } from 'vue';
import { useStageScale } from '@/composables/useStageScale';

const { scale, offsetX, offsetY } = useStageScale();

const stageStyle = computed(() => ({
  transform: `translate(${offsetX.value}px, ${offsetY.value}px) scale(${scale.value})`
}));
</script>

<template>
  <div class="viewport">
    <div class="stage" :style="stageStyle">
      <section class="screen">
        <div class="screen__bg" aria-hidden="true">
          <div class="glow glow--cyan" />
          <div class="glow glow--magenta" />
          <div class="glow glow--blurple" />
          <div class="screen__scrim" />
        </div>
        <slot />
      </section>
    </div>
  </div>
</template>
