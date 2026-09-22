<script setup lang="ts">
/**
 * OperationLogView —— 操作审计日志
 * 代码仓库规范要求：任何一行代码 / 任何一次关键配置变更，
 * 作者、时间、原因都必须可追溯；敏感操作必须留审计日志。
 */
import { computed, ref } from 'vue';
import { adminApi } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const moduleName = ref('');
const result = ref('');
const extraQuery = computed(() => ({
  ...(moduleName.value ? { module: moduleName.value } : {}),
  ...(result.value ? { result: result.value } : {})
}));

const columns: TableColumn[] = [
  { prop: 'username', label: '操作人', width: 120 },
  { prop: 'module', label: '所属模块', width: 140 },
  { prop: 'action', label: '操作类型', width: 100 },
  { prop: 'target', label: '操作对象', minWidth: 180 },
  { prop: 'ip', label: '来源 IP', width: 140, mono: true },
  { prop: 'ua', label: '终端环境', minWidth: 200 },
  { prop: 'result', label: '结果', width: 90, slot: 'col-result' },
  { prop: 'operatedAt', label: '操作时间', width: 180, mono: true }
];

const MODULES = ['用户管理', '系统参数', '告警管理', '产线主数据', '能源报表', '计量点位'];
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" plain @click="notImplemented('导出审计日志')">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="审计要求"
      description="审计日志仅可追加、不可修改或删除；总部对各工厂代码库与系统操作具备审计权限。"
    />

    <TablePage
      :columns="columns"
      :fetch="adminApi.getOperationLogs"
      :extra-query="extraQuery"
      keyword-placeholder="操作人 / 对象 / IP"
      :max-height="560"
    >
      <template #filters>
        <el-select v-model="moduleName" placeholder="全部模块" clearable style="width: 150px">
          <el-option v-for="m in MODULES" :key="m" :label="m" :value="m" />
        </el-select>
        <el-select v-model="result" placeholder="全部结果" clearable style="width: 120px">
          <el-option label="成功" value="success" />
          <el-option label="失败" value="failed" />
        </el-select>
      </template>

      <template #col-result="{ row }">
        <el-tag :type="row.result === 'success' ? 'success' : 'danger'" size="small">
          {{ row.result === 'success' ? '成功' : '失败' }}
        </el-tag>
      </template>
    </TablePage>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
</style>
