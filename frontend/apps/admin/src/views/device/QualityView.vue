<script setup lang="ts">
/**
 * QualityView —— 数据质量
 * 各产线整点数据的应报 / 实收 / 缺失统计。
 * 该页是「大屏数值可信度」的体检表：缺失率高说明采集链路有问题，
 * 此时大屏上的所有折算指标（单位电耗、水耗强度）都不可直接用于考核。
 */
import { computed, onMounted, ref } from 'vue';
import { adminApi, type DataQualityRow } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import ChartPanel from '@/components/ChartPanel.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { simpleBarOption } from '@/utils/charts';
import { notImplemented } from '@/utils/pending';

const rows = ref<DataQualityRow[]>([]);
const loading = ref(true);

onMounted(async () => {
  try {
    rows.value = await adminApi.getQuality();
  } finally {
    loading.value = false;
  }
});

const avgRate = computed(() =>
  rows.value.length ? rows.value.reduce((a, r) => a + r.rate, 0) / rows.value.length : 0
);
const totalMissing = computed(() => rows.value.reduce((a, r) => a + r.missingPoints, 0));

const columns: TableColumn[] = [
  { prop: 'lineCode', label: '产线', width: 90 },
  { prop: 'lineName', label: '产线名称', minWidth: 150 },
  { prop: 'expectPoints', label: '应报点数', width: 110, mono: true },
  { prop: 'receivedPoints', label: '实收点数', width: 110, mono: true },
  { prop: 'missingPoints', label: '缺失点数', width: 110, mono: true, slot: 'col-missing' },
  { prop: 'rate', label: '完整率', width: 130, slot: 'col-rate' },
  { prop: 'missingHours', label: '缺失时段', minWidth: 180 },
  { prop: 'lastAt', label: '最后上报', width: 180, mono: true }
];

function fetchQuality(query: { page: number; pageSize: number; keyword?: string }) {
  const kw = String(query.keyword ?? '').toLowerCase();
  const list = kw ? rows.value.filter((r) => `${r.lineCode}${r.lineName}`.toLowerCase().includes(kw)) : rows.value;
  return Promise.resolve({ list, total: list.length, page: 1, pageSize: 20 });
}

const chartOption = computed(() =>
  simpleBarOption(
    rows.value.map((r) => r.lineCode),
    rows.value.map((r) => +(r.rate.toFixed(1))),
    { name: '完整率', unit: '%', color: '#1664ff', labelInterval: 0 }
  )
);
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="notImplemented('触发补采任务')"><el-icon><Refresh /></el-icon>&nbsp;触发补采</el-button>
        <el-button type="primary" plain @click="notImplemented('导出质量报告')">
          <el-icon><Download /></el-icon>&nbsp;导出报告
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="mb-16"
      title="口径提醒"
      description="完整率低时，单位电耗等折算指标会失真，不可直接用于能耗考核。缺失时段需先在采集侧定位原因（网关离线 / 表计故障 / 通讯中断），再重新补采。"
    />

    <div class="stat-grid">
      <StatCard label="平均上报完整率" :value="avgRate.toFixed(1)" unit="%" color="#1664ff" :loading="loading" :trend="avgRate >= 98 ? 'down' : 'up'" foot="低于 98% 需排查采集链路" />
      <StatCard label="缺失总点数" :value="totalMissing" unit="点" color="#d7312a" :loading="loading" foot="应报未报的整点数量合计" />
      <StatCard label="监测量" :value="rows.length" unit="条产线" color="#2a814b" :loading="loading" foot="每条产线每日应有 24 个整点" />
      <StatCard label="补采机制" value="断点续跑" color="#bd7e00" :loading="loading" foot="长期任务失败后可从断点继续" />
    </div>

    <ChartPanel title="各产线数据完整率" :option="chartOption" :height="240" note="单位：%" class="mt-16" />

    <div class="mt-16">
      <TablePage :columns="columns" :fetch="fetchQuality" keyword-placeholder="产线编号 / 名称">
        <template #col-missing="{ row }">
          <span :class="row.missingPoints > 0 ? 't-danger' : 't-subtle'">{{ row.missingPoints }}</span>
        </template>

        <template #col-rate="{ row }">
          <el-progress
            :percentage="row.rate"
            :stroke-width="6"
            :color="row.rate >= 99 ? '#2a814b' : row.rate >= 90 ? '#bd7e00' : '#d7312a'"
          />
        </template>
      </TablePage>
    </div>
  </div>
</template>

<style scoped lang="scss">
.stat-grid { margin-bottom: 16px; }
.mb-16 { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
</style>
