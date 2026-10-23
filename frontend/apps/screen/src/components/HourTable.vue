<script setup lang="ts">
/**
 * HourTable —— 本线小时明细表
 * 排序：当前小时 → 00:00，再接 当前+1 → 23:00（计划态）。
 * 同时统计应报 / 实收点数，供「数据上报质量」复用。
 */
import { computed } from 'vue';
import { HOUR_QUALITY_LABEL, dec1, int, pad2, rise, thresholds, type HourPoint, type LineData } from '@eep/shared';

const props = defineProps<{
  line: LineData;
  /** 排序锚点：从这一小时往回排（历史整日 = 23） */
  currentHour: number;
  /** 真正「还没结束」的小时；查历史整日传 null，此时**没有任何一格是进行中**（B-522） */
  ongoingHour?: number | null;
}>();

interface Row {
  hour: number;
  clock: number;
  isCurrent: boolean;
  outputText: string;
  kwhText: string;
  unitText: string;
  unitWarn: boolean;
  deltaText: string;
  deltaColor: string;
  status: string;
  statusColor: string;
  cls: string;
}

/**
 * 状态判定。
 *
 * ⚠ 「进行中」只能由「这个小时还没结束」推出（`h === ongoingHour`），**不能看 `partial`**。
 *   `partial` 的真实含义是「本小时合计不完整」，已结束的整点也会因为有表缺数 /
 *   端点偏离整点 / 尖峰被排除而被标成 partial —— 那是**数据异常**。
 *   以前混用的后果：8 点那个异常小时到了 10 点还显示「进行中」，
 *   现场以为等一会儿就会补上，其实它永远不会再变了（B-518）。
 *
 * `ongoingHour = null` = 看的是已经过完的一天，此时**没有任何一格是进行中**（B-522）。
 */
function statusOf(
  s: HourPoint,
  h: number,
  ongoingHour: number | null
): { text: string; color: string; cls: string } {
  if (s.future) return { text: '计划', color: 'var(--t5)', cls: 'is-future' };
  if (h === ongoingHour) return { text: '进行中', color: 'var(--cyan)', cls: '' };
  if (s.stopped) return { text: '停线', color: 'var(--magenta)', cls: '' };
  if (s.quality && s.quality !== 'ok') {
    return { text: HOUR_QUALITY_LABEL[s.quality] ?? '异常', color: 'var(--amber)', cls: 'is-missing' };
  }
  // 接口没带 quality（老数据）时退回按值判定，总之不能报「正常」
  if (s.kwh === null || s.kwh === undefined) {
    return { text: HOUR_QUALITY_LABEL.missing, color: 'var(--amber)', cls: 'is-missing' };
  }
  return { text: '正常', color: 'var(--green)', cls: '' };
}

const rows = computed<Row[]>(() => {
  const { line, currentHour } = props;
  /**
   * 进行中的小时：**只有 `undefined`（调用方没传）才退回锚点**。
   * 写成 `ongoingHour ?? currentHour` 会把明确传进来的 `null` 也当成没传 ——
   * `null` 是「这一天已经过完，没有进行中的小时」，
   * 退回锚点就等于让历史日的 23 点重新显示「进行中」（B-522 的原症状）。
   */
  const ongoing = props.ongoingHour === undefined ? currentHour : props.ongoingHour;
  const order: number[] = [];
  for (let a = currentHour; a >= 0; a--) order.push(a);
  for (let b = currentHour + 1; b <= 23; b++) order.push(b);

  return order.map((h) => {
    const s = line.hours[h];
    const prev = h > 0 ? line.hours[h - 1] : null;
    const { text: status, color: statusColor, cls } = statusOf(s, h, ongoing);

    const dlt =
      s.unitKwh !== null && prev && prev.unitKwh !== null && !s.partial && !s.stopped
        ? rise(s.unitKwh, prev.unitKwh)
        : null;

    // 电量与后台同一口径：1 位小数（旧规则「≥20 取整」让两边每格都差 0.x，B-524）
    const kwhText = s.future || s.kwh === null ? '--' : dec1(s.kwh);
    const warn = s.unitKwh !== null && s.unitKwh > thresholds.unitKwhHigh;

    return {
      hour: h,
      clock: s.clock,
      isCurrent: h === ongoing,
      outputText: s.future ? '--' : int(s.output),
      kwhText,
      unitText: s.unitKwh === null ? '--' : s.unitKwh.toFixed(2),
      unitWarn: warn,
      deltaText: dlt === null ? '--' : `${dlt > 0 ? '+' : ''}${dlt.toFixed(1)}%`,
      deltaColor: dlt === null ? '#5f6ba8' : dlt > 0 ? '#ec48bd' : '#35ed7e',
      status,
      statusColor,
      cls
    };
  });
});

const received = computed(() => props.line.hours.filter((s) => !s.future && s.kwh !== null && s.kwh !== undefined).length);
const expect = computed(() => props.line.hours.filter((s) => !s.future).length);

defineExpose({ received, expect });
</script>

<template>
  <div class="table-wrap">
    <table class="dt">
      <thead>
        <tr>
          <th>时间</th>
          <th>产量 瓶</th>
          <th>电耗 kWh</th>
          <th>kWh/千瓶</th>
          <th>环比</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.hour" :class="r.cls">
          <td>
            {{ pad2(r.clock) }}:00
            <span v-if="r.isCurrent" style="color: var(--cyan); font-size: 9px">当前</span>
          </td>
          <td>{{ r.outputText }}</td>
          <td>{{ r.kwhText }}</td>
          <td :style="{ color: r.unitWarn ? '#ffb020' : '#e8ecff' }">{{ r.unitText }}</td>
          <td :style="{ color: r.deltaColor }">{{ r.deltaText }}</td>
          <td :style="{ color: r.statusColor }">{{ r.status }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
