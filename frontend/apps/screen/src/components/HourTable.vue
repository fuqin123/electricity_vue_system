<script setup lang="ts">
/**
 * HourTable —— 本线小时明细表
 * 排序：当前小时 → 00:00，再接 当前+1 → 23:00（计划态）。
 * 同时统计应报 / 实收点数，供「数据上报质量」复用。
 */
import { computed } from 'vue';
import { int, pad2, rise, thresholds, type LineData } from '@eep/shared';

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

const rows = computed<Row[]>(() => {
  const { line, currentHour } = props;
  const order: number[] = [];
  for (let a = currentHour; a >= 0; a--) order.push(a);
  for (let b = currentHour + 1; b <= 23; b++) order.push(b);

  return order.map((h) => {
    const s = line.hours[h];
    const prev = h > 0 ? line.hours[h - 1] : null;
    let cls = s.future ? 'is-future' : '';
    let status = '';
    let statusColor = '';

    if (s.future) {
      status = '计划';
      statusColor = 'var(--t5)';
    } else {
      if (s.kwh === null || s.kwh === undefined) {
        status = '电表缺失';
        statusColor = 'var(--amber)';
        cls = 'is-missing';
      }
      if (s.stopped) {
        status = '停线';
        statusColor = 'var(--magenta)';
      } else if (s.partial) {
        status = '进行中';
        statusColor = 'var(--cyan)';
      } else if (!status) {
        status = '正常';
        statusColor = 'var(--green)';
      }
    }

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
