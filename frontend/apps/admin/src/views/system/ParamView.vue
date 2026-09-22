<script setup lang="ts">
/**
 * ParamView —— 系统参数
 * 能耗阈值、采集策略、大屏参数与运维策略。
 * ⚠ 这些参数是告警规则的唯一真值来源，修改后必须同步通知能源管理员。
 */
import { adminApi } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const columns: TableColumn[] = [
  { prop: 'group', label: '分组', width: 130, slot: 'col-group' },
  { prop: 'key', label: '参数键', minWidth: 230 },
  { prop: 'name', label: '参数名称', width: 190 },
  { prop: 'value', label: '参数值', width: 150, mono: true },
  { prop: 'unit', label: '单位', width: 110, mono: true },
  { prop: 'remark', label: '说明', minWidth: 260 },
  { prop: 'id', label: '操作', width: 100, slot: 'col-op' }
];

const GROUP_TYPE: Record<string, 'primary' | 'success' | 'warning' | 'info'> = {
  能耗阈值: 'primary',
  采集策略: 'success',
  大屏参数: 'info',
  运维策略: 'warning',
  告警通知: 'warning'
};
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="notImplemented('恢复默认参数')"><el-icon><RefreshLeft /></el-icon>&nbsp;恢复默认</el-button>
        <el-button type="primary" @click="notImplemented('新增参数')"><el-icon><Plus /></el-icon>&nbsp;新增参数</el-button>
      </template>
    </PageHeader>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="mb-16"
      title="改动影响面"
      description="能耗阈值类参数会直接影响大屏与后台的告警判定结果；采集策略类参数会改变网关负载。修改前请确认影响范围，并保留变更记录。"
    />

    <TablePage :columns="columns" :fetch="adminApi.getParams" keyword-placeholder="参数键 / 名称 / 说明" :page-size="20">
      <template #col-group="{ row }">
        <el-tag :type="GROUP_TYPE[row.group] ?? 'info'" size="small">{{ row.group }}</el-tag>
      </template>

      <template #col-op="{ row }">
        <el-button link type="primary" @click="notImplemented(`编辑参数 ${row.key}`)">编辑</el-button>
      </template>
    </TablePage>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
</style>
