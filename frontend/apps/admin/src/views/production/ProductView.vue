<script setup lang="ts">
/**
 * ProductView —— 班次与产品
 * 班次时段定义与产品主数据。班次直接影响大屏「白班 / 夜班」显示与班次达成率口径。
 */
import { computed, ref } from 'vue';
import { linesConfig, type PageQuery, type PageResult } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

interface ShiftRow {
  id: number;
  code: string;
  name: string;
  start: string;
  end: string;
  crossDay: string;
  remark: string;
}

interface ProductRow {
  id: number;
  code: string;
  name: string;
  spec: string;
  lineCode: string;
  standardUnitKwh: number;
  status: string;
}

const shifts: ShiftRow[] = [
  { id: 1, code: 'DAY', name: '白班', start: '08:00', end: '20:00', crossDay: '否', remark: '与《车间班次管理》一致，大屏按此时段显示' },
  { id: 2, code: 'NIGHT', name: '夜班', start: '20:00', end: '08:00', crossDay: '是', remark: '跨零点，日产量统计不做拆分' }
];

const products: ProductRow[] = linesConfig.map((l, i) => ({
  id: i + 1,
  code: `P-${String(i + 1).padStart(3, '0')}`,
  name: l.product.replace(/\s*\d+ml$/, ''),
  spec: l.product.match(/\d+ml/)?.[0] ?? '—',
  lineCode: l.id,
  standardUnitKwh: [7.8, 7.9, 8.1, 8.3, 8.6, 8.4][i],
  status: '启用'
}));

const tab = ref<'shift' | 'product'>('shift');
const lineFilter = ref('');
const productQuery = computed(() => (lineFilter.value ? { lineCode: lineFilter.value } : {}));

const shiftColumns: TableColumn[] = [
  { prop: 'code', label: '班次编码', width: 120 },
  { prop: 'name', label: '班次名称', width: 100 },
  { prop: 'start', label: '开始时间', width: 100, mono: true },
  { prop: 'end', label: '结束时间', width: 100, mono: true },
  { prop: 'crossDay', label: '是否跨天', width: 90 },
  { prop: 'remark', label: '说明', minWidth: 260 }
];

const productColumns: TableColumn[] = [
  { prop: 'code', label: '产品编码', width: 110 },
  { prop: 'name', label: '产品名称', minWidth: 160 },
  { prop: 'spec', label: '规格', width: 100, mono: true },
  { prop: 'lineCode', label: '主生产线', width: 110 },
  { prop: 'standardUnitKwh', label: '标准单位电耗（kWh/千瓶）', width: 210, mono: true, formatter: (r) => r.standardUnitKwh.toFixed(2) },
  { prop: 'status', label: '状态', width: 90 },
  { prop: 'id', label: '操作', width: 90, slot: 'col-op' }
];

function fetchShifts(query: PageQuery): Promise<PageResult<ShiftRow>> {
  const kw = String(query.keyword ?? '').toLowerCase();
  const list = kw ? shifts.filter((s) => `${s.code}${s.name}`.toLowerCase().includes(kw)) : shifts;
  return Promise.resolve({ list, total: list.length, page: 1, pageSize: 10 });
}

function fetchProducts(query: PageQuery): Promise<PageResult<ProductRow>> {
  const kw = String(query.keyword ?? '').toLowerCase();
  let list = products;
  if (lineFilter.value) list = list.filter((p) => p.lineCode === lineFilter.value);
  if (kw) list = list.filter((p) => `${p.code}${p.name}${p.spec}`.toLowerCase().includes(kw));
  return Promise.resolve({ list, total: list.length, page: 1, pageSize: 10 });
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" @click="notImplemented('新增班次 / 产品')">
          <el-icon><Plus /></el-icon>&nbsp;新增
        </el-button>
      </template>
    </PageHeader>

    <el-tabs v-model="tab" class="tabs-card">
      <el-tab-pane label="班次定义" name="shift">
        <TablePage :columns="shiftColumns" :fetch="fetchShifts" keyword-placeholder="班次编码 / 名称" :op-width="150">
          <template #op="{ row }">
            <el-button link type="primary" @click="notImplemented(`编辑班次 ${row.name}`)">编辑</el-button>
          </template>
        </TablePage>
      </el-tab-pane>

      <el-tab-pane label="产品主数据" name="product">
        <TablePage :columns="productColumns" :fetch="fetchProducts" :extra-query="productQuery" keyword-placeholder="产品编码 / 名称">
          <template #filters>
            <el-select v-model="lineFilter" placeholder="全部产线" clearable style="width: 140px">
              <el-option v-for="l in linesConfig" :key="l.id" :label="l.name" :value="l.id" />
            </el-select>
          </template>
          <template #col-op="{ row }">
            <el-button link type="primary" @click="notImplemented(`编辑产品 ${row.name}`)">编辑</el-button>
          </template>
        </TablePage>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped lang="scss">
.tabs-card {
  :deep(.el-tabs__header) { margin-bottom: 12px; }
  :deep(.el-tabs__item) { font-size: 13px; }
}
</style>
