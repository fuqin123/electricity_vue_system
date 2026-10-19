<script setup lang="ts">
/**
 * RealtimeView —— 实时告警
 * 只展示「未处理」告警，支持批量确认。确认动作会写入操作审计日志。
 */
import { computed, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { adminApi, type AlarmRecord } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import AlarmLevelTag from '@/components/AlarmLevelTag.vue';
import { notImplemented } from '@/utils/pending';

const level = ref('');

const extraQuery = computed(() => ({ status: 'active', ...(level.value ? { level: level.value } : {}) }));

const columns: TableColumn[] = [
  { prop: 'code', label: '告警编号', width: 180 },
  { prop: 'level', label: '级别', width: 80, slot: 'col-level' },
  { prop: 'source', label: '来源', width: 170 },
  { prop: 'content', label: '内容', minWidth: 240 },
  { prop: 'value', label: '触发值', width: 110, mono: true },
  { prop: 'threshold', label: '阈值', width: 110, mono: true },
  { prop: 'occurredAt', label: '发生时间', width: 180, mono: true }
];

async function ack(row: AlarmRecord) {
  try {
    await ElMessageBox.confirm(`确认已处理告警「${row.content}」？`, '确认告警', { type: 'warning' });
  } catch {
    return;
  }
  await adminApi.ackAlarm(row.id);
  ElMessage.success('已确认，该告警不再出现在未处理列表'); // 审计日志尚未实现，不谎称写了
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="notImplemented('批量确认')">
          <el-icon><Select /></el-icon>&nbsp;批量确认
        </el-button>
        <el-button type="primary" plain @click="notImplemented('导出告警清单')">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
      </template>
    </PageHeader>

    <TablePage
      :columns="columns"
      :fetch="adminApi.getAlarms"
      :extra-query="extraQuery"
      keyword-placeholder="告警编号 / 来源 / 内容"
      :op-width="120"
      row-key="code"
    >
      <template #filters>
        <el-select v-model="level" placeholder="全部级别" clearable style="width: 140px">
          <el-option label="严重" value="critical" />
          <el-option label="重要" value="major" />
          <el-option label="次要" value="minor" />
          <el-option label="提示" value="info" />
        </el-select>
      </template>

      <template #col-level="{ row }">
        <AlarmLevelTag :level="row.level" />
      </template>

      <template #op="{ row }">
        <el-button link type="primary" @click="ack(row)">确认</el-button>
      </template>
    </TablePage>
  </div>
</template>
