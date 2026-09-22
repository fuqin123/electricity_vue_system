<script setup lang="ts">
/**
 * CollectLogView —— 数据采集日志
 * 定时任务约定：必须记录开始时间、结束时间、执行结果与影响行数。
 * 失败需告警（邮件 / 企业微信），长期任务需支持断点续跑。
 */
import { computed, ref } from 'vue';
import { adminApi } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const result = ref('');
const extraQuery = computed(() => (result.value ? { result: result.value } : {}));

const columns: TableColumn[] = [
  { prop: 'taskName', label: '任务名称', minWidth: 200 },
  { prop: 'gateway', label: '来源网关', width: 170 },
  { prop: 'pointCount', label: '点位数', width: 90, mono: true },
  { prop: 'rows', label: '影响行数', width: 110, mono: true },
  { prop: 'durationMs', label: '耗时', width: 110, mono: true, formatter: (r) => `${r.durationMs} ms` },
  { prop: 'result', label: '执行结果', width: 110, slot: 'col-result' },
  { prop: 'message', label: '说明', minWidth: 240 },
  { prop: 'startedAt', label: '开始时间', width: 180, mono: true }
];

const RESULT_MAP: Record<string, { text: string; type: 'success' | 'warning' | 'danger' }> = {
  success: { text: '成功', type: 'success' },
  partial: { text: '部分成功', type: 'warning' },
  failed: { text: '失败', type: 'danger' }
};
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="notImplemented('立即执行采集任务')"><el-icon><Refresh /></el-icon>&nbsp;立即执行</el-button>
        <el-button type="primary" plain @click="notImplemented('导出采集日志')">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="日志留存策略"
      description="采集日志按天分表并保留 90 天，超过保留期的记录自动归档到冷表。本地文件日志同样按大小滚动，至少保留 30 天。"
    />

    <TablePage
      :columns="columns"
      :fetch="adminApi.getCollectLogs"
      :extra-query="extraQuery"
      keyword-placeholder="任务名称 / 网关 / 说明"
      :max-height="560"
      :op-width="130"
    >
      <template #filters>
        <el-select v-model="result" placeholder="全部结果" clearable style="width: 140px">
          <el-option label="成功" value="success" />
          <el-option label="部分成功" value="partial" />
          <el-option label="失败" value="failed" />
        </el-select>
      </template>

      <template #col-result="{ row }">
        <el-tag :type="RESULT_MAP[row.result]?.type ?? 'info'" size="small">
          {{ RESULT_MAP[row.result]?.text ?? row.result }}
        </el-tag>
      </template>

      <template #op="{ row }">
        <el-button link type="primary" @click="notImplemented(`查看任务详情 #${row.id}`)">详情</el-button>
        <el-button v-if="row.result === 'failed'" link type="primary" @click="notImplemented('重跑该任务')">重跑</el-button>
      </template>
    </TablePage>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
</style>
