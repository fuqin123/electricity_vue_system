import { onBeforeUnmount, onMounted, ref } from 'vue';

export const STAGE_W = 1920;
export const STAGE_H = 1080;

/**
 * 1920×1080 舞台等比缩放并居中
 * 大屏在不同分辨率／浏览器缩放下保持像素级构图一致。
 *
 * 支持快捷键 F 切换全屏。
 */
export function useStageScale() {
  const scale = ref(1);
  const offsetX = ref(0);
  const offsetY = ref(0);

  function apply() {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const s = Math.min(vw / STAGE_W, vh / STAGE_H);
    scale.value = s;
    offsetX.value = (vw - STAGE_W * s) / 2;
    offsetY.value = (vh - STAGE_H * s) / 2;
    window.dispatchEvent(new CustomEvent('eep:stage-resize', { detail: { scale: s } }));
  }

  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      void document.documentElement.requestFullscreen?.();
    } else {
      void document.exitFullscreen?.();
    }
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'f' || e.key === 'F') toggleFullscreen();
  }

  onMounted(() => {
    apply();
    window.addEventListener('resize', apply);
    document.addEventListener('keydown', onKeydown);
  });

  onBeforeUnmount(() => {
    window.removeEventListener('resize', apply);
    document.removeEventListener('keydown', onKeydown);
  });

  return { scale, offsetX, offsetY, apply, toggleFullscreen };
}
