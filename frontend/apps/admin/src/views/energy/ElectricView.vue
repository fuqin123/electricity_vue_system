<script setup lang="ts">
/**
 * ElectricView —— 电计量
 * 产线配电柜智能电表的整点电量与单位电耗。
 * ⚠ 电表为累计值（总有功电能），入库时需做差分得到小时用电量；
 *   换表 / 归零 / 通讯中断会在差分后产生尖峰，采集侧必须加异常值过滤。
 */
import { computed, onMounted, ref } from 'vue';
import { adminApi, big, energyModel, int, thresholds, type AdminOverview } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import ChartPanel from '@/components/ChartPanel.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { outputKwhOption } from '@/utils/charts';
import { notImplemented } from '@/utils/pending';

interface HourRow {
  id: number;
  hour: string;
  output: string;
  kwh: string;
  unitKwh: string;
  status: string;
}

const overview = ref<AdminOverview | null>(null);
const loading = ref(true);

onMounted(async () => {
  try {
    overview.value = await adminApi.getOverview();
  } finally {
    loading.value = false;
  }
});

const columns: TableColumn[] = [
  { prop: 'hour', label: '时间', width: 110, mono: true },
  { prop: 'output', label: '产量（瓶）', width: 140, mono: true },
  { prop: 'kwh', label: '电量（kWh）', width: 140, mono: true },
  { prop: 'unitKwh', label: '单位电耗（kWh/千瓶）', width: 180, mono: true },
  { prop: 'status', label: '数据状态', width: 110 },
  { prop: 'remark', label: '备注', minWidth: 200 }
];

/** 今日分时明细：直接由总览快照换算，接真实接口时改为分页接口 */
const rows = computed<HourRow[]>(() => {
  const o = overview.value;
  if (!o) return [];
  const cur = new Date().getHours();
  return o.hourly
    .slice(0, cur + 1)
    .map((h, i) => ({
      id: i + 1,
      hour: h.label,
      output: int(h.output),
      kwh: int(h.kwh),
      unitKwh: h.unitKwh === null || h.unitKwh === undefined ? '--' : h.unitKwh.toFixed(2),
      status: '已入库',
      remark: ''
    }))
    .reverse();
});

function fetchHours(): Promise<{ list: HourRow[]; total: number; page: number; pageSize: number }> {
  return Promise.resolve({ list: rows.value, total: rows.value.length, page: 1, pageSize: 50 });
}

const trendOption = computed(() =>
  overview.value
    ? outputKwhOption(overview.value.hourly, {
        outDiv: 1000,
        kwhDiv: 1000,
        outUnit: '千瓶',
        kwhUnit: '千kWh',
        labelInterval: 1
      })
    : null
);

const kpi = computed(() => overview.value?.kpi);
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="notImplemented('手动补采')"><el-icon><Refresh /></el-icon>&nbsp;手动补采</el-button>
        <el-button type="primary" plain @click="notImplemented('导出电量明细')">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
      </template>
    </PageHeader>

    <div class="stat-grid">
      <StatCard label="今日用电量" :value="big(kpi?.todayKwh ?? 0, '').v" :unit="big(kpi?.todayKwh ?? 0, 'kWh').u" :loading="loading" color="#1664ff" :foot="`折合 ${int(kpi?.todayKwh ?? 0)} kWh`" />
      <StatCard label="今日产量" :value="big(kpi?.todayOutput ?? 0, '').v" :unit="big(kpi?.todayOutput ?? 0, '瓶').u" :loading="loading" color="#387bff" />
      <StatCard
        label="综合单位电耗"
        :value="(kpi?.unitKwh ?? 0).toFixed(2)"
        unit="kWh/千瓶"
        :color="(kpi?.unitKwh ?? 0) > energyModel.targetUnitKwh ? '#d7312a' : '#2a814b'"
        :trend="(kpi?.unitKwh ?? 0) > energyModel.targetUnitKwh ? 'up' : 'down'"
        :loading="loading"
        :foot="`目标 ${energyModel.targetUnitKwh.toFixed(2)} · 告警上限 ${thresholds.unitKwhHigh.toFixed(2)}`"
      />
      <StatCard label="已入库整点数" :value="rows.length" unit="点" :loading="loading" color="#2a814b" foot="整点聚合后入库，原始值保留 7 天" />
    </div>

    <ChartPanel
      title="今日分时产量与电量"
      :option="trendOption"
      :height="280"
      note="虚线柱为未到时段的计划值"
      class="mt-16"
    />

    <div class="mt-16">
      <TablePage
        :columns="columns"
        :fetch="fetchHours"
        keyword-placeholder=""
        :page-size="20"
        :max-height="420"
      >
        <template #toolbar>
          <span class="t-subtle">共 {{ rows.length }} 条整点记录（最新在上）</span>
        </template>
      </TablePage>
    </div>
  </div>
</template>

<style scoped lang="scss">
.stat-grid { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
</style>
