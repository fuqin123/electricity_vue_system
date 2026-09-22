<script setup lang="ts">
/**
 * MeterView —— 计量点位
 * 电 / 水 / 汽 / 气 计量点位的协议、地址与变比系数。
 * ⚠ 电表为累计值，需在采集侧差分；系数填错会直接导致用量整体偏移。
 */
import { computed, ref } from 'vue';
import { adminApi } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const category = ref('');
const protocol = ref('');
const status = ref('');
const extraQuery = computed(() => ({
  ...(category.value ? { category: category.value } : {}),
  ...(protocol.value ? { protocol: protocol.value } : {}),
  ...(status.value ? { status: status.value } : {})
}));

const columns: TableColumn[] = [
  { prop: 'code', label: '点位编号', width: 150 },
  { prop: 'name', label: '点位名称', minWidth: 180 },
  { prop: 'category', label: '类别', width: 80 },
  { prop: 'lineCode', label: '所属产线', width: 100 },
  { prop: 'protocol', label: '通讯协议', width: 130 },
  { prop: 'address', label: '地址 / 从站', width: 200 },
  { prop: 'unit', label: '单位', width: 80 },
  { prop: 'factor', label: '变比系数', width: 100, mono: true },
  { prop: 'status', label: '状态', width: 100, slot: 'col-status' },
  { prop: 'lastAt', label: '最后上报', width: 180, mono: true }
];

const STATUS_MAP: Record<string, { text: string; type: 'success' | 'danger' | 'warning' }> = {
  online: { text: '在线', type: 'success' },
  offline: { text: '离线', type: 'danger' },
  fault: { text: '故障', type: 'warning' }
};
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="notImplemented('点位批量导入')"><el-icon><Upload /></el-icon>&nbsp;批量导入</el-button>
        <el-button type="primary" @click="notImplemented('新增计量点位')">
          <el-icon><Plus /></el-icon>&nbsp;新增点位
        </el-button>
      </template>
    </PageHeader>

    <TablePage
      :columns="columns"
      :fetch="adminApi.getMeters"
      :extra-query="extraQuery"
      keyword-placeholder="点位编号 / 名称 / 地址"
      :op-width="150"
      :max-height="560"
    >
      <template #filters>
        <el-select v-model="category" placeholder="全部类别" clearable style="width: 120px">
          <el-option label="电" value="电" />
          <el-option label="水" value="水" />
          <el-option label="汽" value="汽" />
          <el-option label="气" value="气" />
        </el-select>
        <el-select v-model="protocol" placeholder="全部协议" clearable style="width: 140px">
          <el-option label="Modbus TCP" value="Modbus TCP" />
          <el-option label="Modbus RTU" value="Modbus RTU" />
          <el-option label="OPC UA" value="OPC UA" />
          <el-option label="MQTT" value="MQTT" />
          <el-option label="DL/T645" value="DL/T645" />
        </el-select>
        <el-select v-model="status" placeholder="全部状态" clearable style="width: 120px">
          <el-option label="在线" value="online" />
          <el-option label="离线" value="offline" />
          <el-option label="故障" value="fault" />
        </el-select>
      </template>

      <template #col-status="{ row }">
        <el-tag :type="STATUS_MAP[row.status]?.type ?? 'info'" size="small">
          {{ STATUS_MAP[row.status]?.text ?? row.status }}
        </el-tag>
      </template>

      <template #op="{ row }">
        <el-button link type="primary" @click="notImplemented(`测试连通性 ${row.code}`)">测试</el-button>
        <el-button link type="primary" @click="notImplemented(`编辑点位 ${row.code}`)">编辑</el-button>
      </template>
    </TablePage>
  </div>
</template>
