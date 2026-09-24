<script setup lang="ts">
/**
 * HourTable —— 本线小时明细表
 * 排序：当前小时 → 00:00，再接 当前+1 → 23:00（计划态）。
 * 同时统计应报 / 实收点数，供「数据上报质量」复用。
 */
import { computed } from 'vue';
import { HOUR_QUALITY_LABEL, int, pad2, rise, thresholds, type HourPoint, type LineData } from '@eep/shared';

const props = defineProps<{
  line: LineData;
  currentHour: number;
}>();

interface Row {
  hour: number;
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
 * ⚠ 「进行中」只能由「这个小时还没结束」推出（`h === currentHour`），**不能看 `partial`**。
 *   `partial` 的真实含义是「本小时合计不完整」，已结束的整点也会因为有表缺数 /
 *   端点偏离整点 / 尖峰被排除而被标成 partial —— 那是**数据异常**。
 *   以前混用的后果：8 点那个异常小时到了 10 点还显示「进行中」，
 *   现场以为等一会儿就会补上，其实它永远不会再变了（B-518）。
 */
function statusOf(s: HourPoint, h: number, currentHour: number): { text: string; color: string; cls: string } {
  if (s.future) return { text: '计划', color: 'var(--t5)', cls: 'is-future' };
  if (h === currentHour) return { text: '进行中', color: 'var(--cyan)', cls: '' };
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
  const order: number[] = [];
  for (let a = currentHour; a >= 0; a--) order.push(a);
  for (let b = currentHour + 1; b <= 23; b++) order.push(b);

  return order.map((h) => {
    const s = line.hours[h];
    const prev = h > 0 ? line.hours[h - 1] : null;
    const { text: status, color: statusColor, cls } = statusOf(s, h, currentHour);

    const dlt =
      s.unitKwh !== null && prev && prev.unitKwh !== null && !s.partial && !s.stopped
        ? rise(s.unitKwh, prev.unitKwh)
        : null;

    const warn = s.unitKwh !== null && s.unitKwh > thresholds.unitKwhHigh;
    // 刚过整点时电量可能不足 20kWh，保留 1 位小数，避免与单位电耗对不上
    const kwhText =
      s.future || s.kwh === null
        ? '--'
        : s.kwh < 20
          ? (Math.round(s.kwh * 10) / 10).toFixed(1)
          : int(s.kwh);

    return {
      hour: h,
      isCurrent: h === currentHour,
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
            {{ pad2(r.hour) }}:00
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
