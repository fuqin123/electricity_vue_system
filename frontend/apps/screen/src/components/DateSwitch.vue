<script setup lang="ts">
/**
 * DateSwitch —— 大屏日期切换（查看历史数据）
 *
 * 为什么做得这么显眼（选了历史日期会变琥珀色 + 明确写「历史数据」）：
 *   大屏是挂在车间墙上的，看的人往往只看一眼数字。如果历史数据与实时数据
 *   长得一样，很容易把几天前的用电量当成「现在」的读数来做判断。
 *
 * 历史模式下上层会**停掉自动刷新**（见 stores/energy.ts 的 isHistory）：
 * 历史数据不会再变，继续刷新只是白耗接口。
 */
import { computed } from 'vue';

const props = defineProps<{
  /** 当前日期 YYYY-MM-DD */
  modelValue: string;
}>();
const emit = defineEmits<{ 'update:modelValue': [string] }>();

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

function fmt(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 今天（每次求值都取系统时间，跨零点后自动更新） */
const today = computed(() => fmt(new Date()));
const isHistory = computed(() => props.modelValue !== today.value);

function shift(days: number) {
  const [y, m, d] = props.modelValue.split('-').map(Number);
  const next = fmt(new Date(y, m - 1, d + days));
  // 不允许翻到未来：未来日期没有任何数据，翻过去只会看到一片空
  if (next > today.value) return;
  emit('update:modelValue', next);
}

function onInput(e: Event) {
  const v = (e.target as HTMLInputElement).value;
  if (v) emit('update:modelValue', v);
}
</script>

<template>
  <div class="date-switch" :class="{ 'is-history': isHistory }">
    <button type="button" class="date-switch__step" title="前一天" @click="shift(-1)">‹</button>
    <label class="date-switch__field">
      <input type="date" :value="modelValue" :max="today" @change="onInput" />
    </label>
    <button
      type="button"
      class="date-switch__step"
      title="后一天"
      :disabled="modelValue >= today"
      @click="shift(1)"
    >
      ›
    </button>
    <span v-if="isHistory" class="date-switch__badge">
      <button type="button" @click="emit('update:modelValue', today)">历史数据 · 回到今日</button>
    </span>
    <span v-else class="date-switch__badge date-switch__badge--live">实时</span>
  </div>
</template>
