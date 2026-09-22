<script setup lang="ts">
/**
 * LoginLogView —— 登录日志
 * 登录来源、终端与失败原因。连续失败会被判定为暴力破解并短期锁定。
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
  { prop: 'username', label: '用户名', width: 120 },
  { prop: 'ip', label: '来源 IP', width: 140, mono: true },
  { prop: 'location', label: '来源位置', minWidth: 200 },
  { prop: 'browser', label: '浏览器', width: 130 },
  { prop: 'os', label: '操作系统', width: 130 },
  { prop: 'result', label: '结果', width: 90, slot: 'col-result' },
  { prop: 'message', label: '说明', minWidth: 260 },
  { prop: 'loginAt', label: '登录时间', width: 180, mono: true }
];
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" plain @click="notImplemented('导出登录日志')">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
      </template>
    </PageHeader>

    <TablePage
      :columns="columns"
      :fetch="adminApi.getLoginLogs"
      :extra-query="extraQuery"
      keyword-placeholder="用户名 / IP / 位置"
      :max-height="560"
    >
      <template #filters>
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
