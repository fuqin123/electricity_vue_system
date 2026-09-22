<script setup lang="ts">
/**
 * LineList —— 六条产线实时数据行
 * 整点刚过时当前小时只累计了几分钟，展示整点预期值并标注「预计」，
 * 避免读数被误判为暴跌。
 */
import { useRouter } from 'vue-router';
import { pad2, thresholds, type LineData } from '@eep/shared';

const props = defineProps<{ lines: LineData[]; currentHour: number }>();
const router = useRouter();

const STATUS: Record<LineData['status'], { t: string; c: string }> = {
  running: { t: '运行中', c: '#35ed7e' },
  slow: { t: '低产', c: '#ffb020' },
  stopped: { t: '停线', c: '#ec48bd' }
};

function rowClass(l: LineData) {
  return l.status === 'slow' ? 'is-warn' : l.status === 'stopped' ? 'is-idle' : '';
}

function display(l: LineData) {
  const s = l.hours[props.currentHour] ?? {};
  return s.partial ? (s.projected ?? 0) : (s.output ?? 0);
}

function unitLabel(l: LineData) {
  const s = l.hours[props.currentHour] ?? {};
  return s.partial ? '千瓶/h · 预计' : '千瓶/h';
}

function unitKwh(l: LineData) {
  return l.hours[props.currentHour]?.unitKwh ?? null;
}

function go(l: LineData) {
  void router.push({ name: 'line', params: { lineId: l.id } });
}

void pad2;
</script>

<template>
  <div class="list-wrap">
    <div
      v-for="l in lines"
      :key="l.id"
      class="line-row"
      :class="rowClass(l)"
      role="button"
      tabindex="0"
      @click="go(l)"
      @keydown.enter="go(l)"
    >
      <div class="line-row__name">
        <i :style="{ background: STATUS[l.status].c }" />
        <b>{{ l.name }}</b>
        <em :style="{ color: STATUS[l.status].c }">{{ STATUS[l.status].t }}</em>
      </div>
      <div class="line-row__vals">
        <span class="v">
          <b>{{ (display(l) / 1000).toFixed(1) }}</b>
          <span>{{ unitLabel(l) }}</span>
        </span>
        <span class="v">
          <b :class="{ 'c-amber': unitKwh(l) !== null && (unitKwh(l) as number) > thresholds.unitKwhHigh }">
            {{ unitKwh(l) === null ? '--' : (unitKwh(l) as number).toFixed(2) }}
          </b>
          <span>kWh/千瓶</span>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.list-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
  height: 100%;
  overflow: hidden;
}
</style>
