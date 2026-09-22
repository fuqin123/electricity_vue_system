<script setup lang="ts">
/**
 * LineListView —— 产线主数据
 * 维护产线编号、产品、额定产能与计量点位绑定关系（大屏与后台共用同一主数据）。
 */
import { computed, ref } from 'vue';
import { adminApi, int } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const status = ref('');

const extraQuery = computed(() => (status.value ? { status: status.value } : {}));

const columns: TableColumn[] = [
  { prop: 'code', label: '产线编号', width: 100, formatter: (r) => String(r.code) },
  { prop: 'name', label: '产线名称', minWidth: 150 },
  { prop: 'product', label: '当前产品', minWidth: 160 },
  { prop: 'rated', label: '额定产能（瓶/时）', width: 160, mono: true, formatter: (r) => int(r.rated) },
  { prop: 'workshop', label: '所属车间', width: 140 },
  { prop: 'meterNo', label: '电表编号', width: 150 },
  { prop: 'counterNo', label: '计数器编号', width: 150 },
  { prop: 'status', label: '状态', width: 100, slot: 'col-status' },
  { prop: 'commissionedAt', label: '投产日期', width: 130 }
];

const STATUS_MAP: Record<string, { text: string; type: 'success' | 'info' | 'warning' }> = {
  running: { text: '运行中', type: 'success' },
  idle: { text: '待机', type: 'info' },
  maintenance: { text: '维护中', type: 'warning' }
};

function exportCsv() {
  notImplemented('导出产线主数据', '建议后端实现 GET /api/admin/lines/export?format=xlsx');
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="exportCsv"><el-icon><Download /></el-icon>&nbsp;导出</el-button>
        <el-button type="primary" @click="notImplemented('新增产线')">
          <el-icon><Plus /></el-icon>&nbsp;新增产线
        </el-button>
      </template>
    </PageHeader>

    <TablePage :columns="columns" :fetch="adminApi.getLines" :extra-query="extraQuery" keyword-placeholder="产线编号 / 名称 / 产品" :op-width="130">
      <template #filters>
        <el-select v-model="status" placeholder="全部状态" clearable style="width: 140px">
          <el-option label="运行中" value="running" />
          <el-option label="待机" value="idle" />
          <el-option label="维护中" value="maintenance" />
        </el-select>
      </template>

      <template #col-status="{ row }">
        <el-tag :type="STATUS_MAP[row.status]?.type ?? 'info'" size="small">
          {{ STATUS_MAP[row.status]?.text ?? row.status }}
        </el-tag>
      </template>

      <template #op="{ row }">
        <el-button link type="primary" @click="notImplemented(`编辑产线 ${row.code}`)">编辑</el-button>
        <el-button link type="primary" @click="notImplemented(`绑定计量点位 ${row.code}`)">绑定点位</el-button>
      </template>
    </TablePage>
  </div>
</template>
