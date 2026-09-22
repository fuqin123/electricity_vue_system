<script setup lang="ts">
/**
 * ReportView —— 能源报表
 * 报表模板管理 + 生成记录。本期为模板与记录的展示骨架，
 * 定时生成与推送逻辑后续接入。
 */
import { onMounted, ref } from 'vue';
import { adminApi, type PageQuery, type PageResult, type ReportTemplate } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const templates = ref<ReportTemplate[]>([]);

onMounted(async () => {
  templates.value = await adminApi.getReports();
});

const columns: TableColumn[] = [
  { prop: 'name', label: '报表名称', minWidth: 200 },
  { prop: 'cycle', label: '周期', width: 90 },
  { prop: 'scope', label: '统计范围', width: 140 },
  { prop: 'format', label: '导出格式', width: 100, formatter: (r) => r.format.toUpperCase() },
  { prop: 'enabled', label: '状态', width: 100, slot: 'col-enabled' },
  { prop: 'lastGeneratedAt', label: '最近生成时间', width: 190, mono: true },
  { prop: 'id', label: '操作', width: 190, slot: 'col-op' }
];

function fetchTemplates(query: PageQuery): Promise<PageResult<ReportTemplate>> {
  const kw = String(query.keyword ?? '').toLowerCase();
  const list = kw
    ? templates.value.filter((t) => `${t.name}${t.cycle}${t.scope}`.toLowerCase().includes(kw))
    : templates.value;
  return Promise.resolve({ list, total: list.length, page: 1, pageSize: 10 });
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="notImplemented('新建报表模板')"><el-icon><Plus /></el-icon>&nbsp;新建模板</el-button>
        <el-button type="primary" @click="notImplemented('立即生成报表')">
          <el-icon><Document /></el-icon>&nbsp;立即生成
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="定时任务约定"
      description="报表由调度任务生成，每次执行必须记录开始时间、结束时间、执行结果与影响行数；失败需告警（邮件 / 企业微信）并支持断点续跑。"
    />

    <TablePage :columns="columns" :fetch="fetchTemplates" keyword-placeholder="报表名称 / 周期">
      <template #col-enabled="{ row }">
        <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '已启用' : '已停用' }}</el-tag>
      </template>

      <template #col-op="{ row }">
        <el-button link type="primary" @click="notImplemented(`生成「${row.name}」`)">生成</el-button>
        <el-button link type="primary" @click="notImplemented(`预览「${row.name}」`)">预览</el-button>
        <el-button link type="primary" @click="notImplemented(`编辑「${row.name}」`)">编辑</el-button>
      </template>
    </TablePage>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
</style>
