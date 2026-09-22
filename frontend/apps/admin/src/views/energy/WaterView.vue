<script setup lang="ts">
/**
 * WaterView —— 水计量
 * 只有车间进水总表（电磁流量计），无法拆分到线别，因此本页不存在「按产线」维度。
 * 水量按车间合计产量折算，异常判定用的是「较昨日同期」而非绝对值。
 */
import { computed, onMounted, ref } from 'vue';
import { energyApi, energyModel, int, rise, round2, type EnergySnapshot } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import ChartPanel from '@/components/ChartPanel.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { simpleBarOption } from '@/utils/charts';
import { notImplemented } from '@/utils/pending';

interface HourRow {
  id: number;
  hour: string;
  rate: string;
  total: string;
  compare: string;
}

const snap = ref<EnergySnapshot | null>(null);
const loading = ref(true);

onMounted(async () => {
  try {
    snap.value = await energyApi.getSnapshot();
  } finally {
    loading.value = false;
  }
});

const util = computed(() => snap.value?.workshop.utility.water ?? null);
const ws = computed(() => snap.value?.workshop ?? null);

const columns: TableColumn[] = [
  { prop: 'hour', label: '时间', width: 110, mono: true },
  { prop: 'rate', label: '瞬时流量（t/h）', width: 160, mono: true },
  { prop: 'total', label: '本小时用水（t）', width: 160, mono: true },
  { prop: 'compare', label: '较上一时段', width: 140, mono: true }
];

const rows = computed<HourRow[]>(() => {
  const u = util.value;
  if (!u) return [];
  const list: HourRow[] = [];
  let acc = 0;
  u.trend.forEach((v, i) => {
    if (v === null) return;
    const prev = u.trend[i - 1];
    acc = round2(acc + v);
    list.push({
      id: i + 1,
      hour: `${String(i).padStart(2, '0')}:00`,
      rate: v.toFixed(2),
      total: v.toFixed(2),
      compare: prev === null || prev === undefined ? '--' : `${rise(v, prev)! > 0 ? '+' : ''}${rise(v, prev)!.toFixed(1)}%`
    });
  });
  return list.reverse();
});

function fetchHours() {
  return Promise.resolve({ list: rows.value, total: rows.value.length, page: 1, pageSize: 50 });
}

const trendOption = computed(() =>
  util.value
    ? simpleBarOption(
        Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, '0')}:00`),
        util.value.trend.map((v) => (v === null ? 0 : v)),
        { name: '水耗', unit: 't', color: '#1664ff', labelInterval: 1 }
      )
    : null
);
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" plain @click="notImplemented('导出水耗明细')">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="数据口径"
      description="车间仅有一块进水总表，水耗无法拆分到产线。折算强度取 2.8 L/瓶（含 CIP 与冷却用水），正式投用前需用真实历史数据回填。"
    />

    <div class="stat-grid">
      <StatCard
        label="今日累计用水"
        :value="int(util?.today ?? 0)"
        unit="t"
        color="#1664ff"
        :loading="loading"
        :trend="(rise(util?.today ?? 0, util?.yesterday ?? 0) ?? 0) > 0 ? 'up' : 'down'"
        :foot="`较昨日同期 ${(rise(util?.today ?? 0, util?.yesterday ?? 0) ?? 0) > 0 ? '+' : ''}${(rise(util?.today ?? 0, util?.yesterday ?? 0) ?? 0).toFixed(1)}%`"
      />
      <StatCard label="昨日同期用水" :value="int(util?.yesterday ?? 0)" unit="t" color="#387bff" :loading="loading" />
      <StatCard label="当前瞬时流量" :value="(util?.rateNow ?? 0).toFixed(2)" unit="t/h" color="#2a814b" :loading="loading" foot="进水管路电磁流量计实时值" />
      <StatCard
        label="折算水耗强度"
        :value="ws && ws.today.output > 0 ? ((util?.today ?? 0) / ws.today.output * 1000).toFixed(2) : '--'"
        unit="L/千瓶"
        color="#bd7e00"
        :loading="loading"
        :foot="`模型基准 ${energyModel.waterPerBottleL} L/瓶`"
      />
    </div>

    <ChartPanel title="今日分时水耗" :option="trendOption" :height="260" note="整点差值，未到时段不显示" class="mt-16" />

    <div class="mt-16">
      <TablePage :columns="columns" :fetch="fetchHours" keyword-placeholder="" :page-size="20" :max-height="400">
        <template #toolbar><span class="t-subtle">共 {{ rows.length }} 条整点记录（最新在上）</span></template>
      </TablePage>
    </div>
  </div>
</template>

<style scoped lang="scss">
.stat-grid { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
.mb-16 { margin-bottom: 16px; }
</style>
