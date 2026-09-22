<script setup lang="ts">
/**
 * BenchmarkBars —— 单位电耗对标
 *
 * 条长 = 目标值 / 该对象实际值（达成度），越长越接近目标。
 * 由于六线单位电耗都落在 7—11 之间，用绝对值做条长会让所有条几乎等长、
 * 失去区分度，因此改用以目标为基准的达成度表达。
 */
import { computed } from 'vue';
import { energyModel, thresholds, type LineData, type WorkshopData } from '@eep/shared';

const props = defineProps<{
  ws: WorkshopData;
  line: LineData;
}>();

interface BenchRow {
  key: string;
  value: number | null;
  cls: string;
  note: string;
}

const rows = computed<BenchRow[]>(() => {
  const valid = props.ws.lines.filter((l) => l.today.unitKwh !== null);
  const avg = valid.length
    ? valid.reduce((a, l) => a + (l.today.unitKwh as number), 0) / valid.length
    : 0;
  let best: LineData | null = null;
  valid.forEach((l) => {
    if (!best || (l.today.unitKwh as number) < (best.today.unitKwh as number)) best = l;
  });

  return [
    { key: `本线 ${props.line.id}`, value: props.line.today.unitKwh, cls: '', note: '实际' },
    { key: '车间平均', value: avg || null, cls: '', note: '六线均值' },
    { key: `最优线 ${best ? (best as LineData).id : '--'}`, value: best ? (best as LineData).today.unitKwh : null, cls: 'is-ref', note: '标杆' },
    { key: '目标值', value: energyModel.targetUnitKwh, cls: 'is-target', note: '考核目标' }
  ];
});

const target = energyModel.targetUnitKwh;

function width(v: number | null) {
  return (v && v > 0 ? Math.min((target / v) * 100, 100) : 0).toFixed(1);
}

function color(r: BenchRow) {
  if (r.value === null) return '#6e78b4';
  if (r.cls === 'is-target') return '#ffb020';
  if (r.cls === 'is-ref') return '#35ed7e';
  return r.value > thresholds.unitKwhHigh ? '#ec48bd' : '#e8ecff';
}
</script>

<template>
  <div class="bench">
    <div v-for="r in rows" :key="r.key" class="bench__row" :class="r.cls">
      <span>{{ r.key }}</span>
      <div class="bench__bar"><i :style="{ width: width(r.value) + '%' }" /></div>
      <b :style="{ color: color(r) }">{{ r.value === null ? '--' : r.value.toFixed(2) }}</b>
    </div>
  </div>
</template>
