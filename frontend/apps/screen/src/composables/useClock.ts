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

/**
 * 轻提示
 *
 * 状态放在模块作用域：此前每次调用 useToast() 都会新建一组 ref，
 * 导致「A 组件调用 toast()、B 组件负责显示」的写法永远显示不出来
 * （App.vue 持有的是自己那一组 ref）。改为共享单例，谁调用都能显示。
 */
const toastText = ref('');
const toastOn = ref(false);
let toastTimer: number | null = null;

function toast(msg: string) {
  toastText.value = msg;
  toastOn.value = true;
  if (toastTimer !== null) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toastOn.value = false;
  }, 3200);
}

export function useToast() {
  return { toastText, toastOn, toast };
}
