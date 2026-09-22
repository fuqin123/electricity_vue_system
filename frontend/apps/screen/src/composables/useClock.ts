import { onBeforeUnmount, onMounted, ref } from 'vue';
import { clockText, shiftText } from '@eep/shared';

/**
 * 秒级时钟 —— 只驱动文本节点，不触发整屏重绘
 */
export function useClock() {
  const now = ref(new Date());
  let timer: number | null = null;

  onMounted(() => {
    now.value = new Date();
    timer = window.setInterval(() => {
      now.value = new Date();
    }, 1000);
  });

  onBeforeUnmount(() => {
    if (timer !== null) window.clearInterval(timer);
  });

  return {
    now,
    clock: () => clockText(now.value),
    shift: () => shiftText(now.value)
  };
}

/** 轻提示 */
export function useToast() {
  const toastText = ref('');
  const on = ref(false);
  let timer: number | null = null;

  function toast(msg: string) {
    toastText.value = msg;
    on.value = true;
    if (timer !== null) window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      on.value = false;
    }, 3200);
  }

  onBeforeUnmount(() => {
    if (timer !== null) window.clearTimeout(timer);
  });

  return { toastText, toastOn: on, toast };
}
