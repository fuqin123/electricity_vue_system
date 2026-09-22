<script setup lang="ts">
/**
 * HistoryView —— 历史告警
 * 所有告警记录与处理结果回溯，是能源管理员做月度归因分析的主要入口。
 */
import { computed, ref } from 'vue';
import { adminApi } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import AlarmLevelTag from '@/components/AlarmLevelTag.vue';
import { notImplemented } from '@/utils/pending';

const level = ref('');
const status = ref('');
const extraQuery = computed(() => ({
  ...(level.value ? { level: level.value } : {}),
  ...(status.value ? { status: status.value } : {})
}));

const columns: TableColumn[] = [
  { prop: 'code', label: '告警编号', width: 180 },
  { prop: 'level', label: '级别', width: 80, slot: 'col-level' },
  { prop: 'source', label: '来源', width: 170 },
  { prop: 'content', label: '内容', minWidth: 240 },
  { prop: 'value', label: '触发值', width: 110, mono: true },
  { prop: 'threshold', label: '阈值', width: 110, mono: true },
  { prop: 'status', label: '处理状态', width: 100, slot: 'col-status' },
  { prop: 'ackedBy', label: '处理人', width: 100 },
  { prop: 'ackedAt', label: '处理时间', width: 180, mono: true },
  { prop: 'occurredAt', label: '发生时间', width: 180, mono: true }
];

const STATUS_MAP: Record<string, { text: string; type: 'danger' | 'warning' | 'success' }> = {
  active: { text: '未处理', type: 'danger' },
  acked: { text: '已确认', type: 'warning' },
  resolved: { text: '已恢复', type: 'success' }
};
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" plain @click="notImplemented('导出告警台账')">
          <el-icon><Download /></el-icon>&nbsp;导出台账
        </el-button>
      </template>
    </PageHeader>

    <TablePage
      :columns="columns"
      :fetch="adminApi.getAlarms"
      :extra-query="extraQuery"
      keyword-placeholder="告警编号 / 来源 / 内容"
      :max-height="560"
    >
      <template #filters>
        <el-select v-model="level" placeholder="全部级别" clearable style="width: 130px">
          <el-option label="严重" value="critical" />
          <el-option label="重要" value="major" />
          <el-option label="次要" value="minor" />
          <el-option label="提示" value="info" />
        </el-select>
        <el-select v-model="status" placeholder="全部状态" clearable style="width: 130px">
          <el-option label="未处理" value="active" />
          <el-option label="已确认" value="acked" />
          <el-option label="已恢复" value="resolved" />
        </el-select>
      </template>

      <template #col-level="{ row }">
        <AlarmLevelTag :level="row.level" />
      </template>

      <template #col-status="{ row }">
        <el-tag :type="STATUS_MAP[row.status]?.type ?? 'info'" size="small">
          {{ STATUS_MAP[row.status]?.text ?? row.status }}
        </el-tag>
      </template>
    </TablePage>
  </div>
</template>
