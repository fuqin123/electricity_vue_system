<script setup lang="ts">
/**
 * DictView —— 数据字典
 * 枚举值统一维护，供下拉框、状态标签等引用，避免硬编码散落各处。
 */
import { computed, ref } from 'vue';
import { adminApi } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const dictType = ref('');
const extraQuery = computed(() => (dictType.value ? { dictType: dictType.value } : {}));

const DICT_TYPES = [
  { label: '产线状态（line_status）', value: 'line_status' },
  { label: '告警级别（alarm_level）', value: 'alarm_level' },
  { label: '计量类别（meter_category）', value: 'meter_category' },
  { label: '通讯协议（protocol）', value: 'protocol' },
  { label: '报表周期（report_cycle）', value: 'report_cycle' }
];

const columns: TableColumn[] = [
  { prop: 'dictType', label: '字典类型', width: 180, mono: true },
  { prop: 'label', label: '显示名称', width: 150 },
  { prop: 'value', label: '字典值', width: 150, mono: true },
  { prop: 'sort', label: '排序', width: 90, align: 'right' },
  { prop: 'status', label: '状态', width: 100, slot: 'col-status' },
  { prop: 'remark', label: '备注', minWidth: 200 },
  { prop: 'id', label: '操作', width: 140, slot: 'col-op' }
];
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" @click="notImplemented('新增字典项')">
          <el-icon><Plus /></el-icon>&nbsp;新增字典项
        </el-button>
      </template>
    </PageHeader>

    <TablePage
      :columns="columns"
      :fetch="adminApi.getDicts"
      :extra-query="extraQuery"
      keyword-placeholder="字典类型 / 名称 / 值"
      :page-size="20"
    >
      <template #filters>
        <el-select v-model="dictType" placeholder="全部字典类型" clearable style="width: 230px">
          <el-option v-for="t in DICT_TYPES" :key="t.value" :label="t.label" :value="t.value" />
        </el-select>
      </template>

      <template #col-status="{ row }">
        <el-tag :type="row.status === 'enabled' ? 'success' : 'info'" size="small">
          {{ row.status === 'enabled' ? '启用' : '停用' }}
        </el-tag>
      </template>

      <template #col-op="{ row }">
        <el-button link type="primary" @click="notImplemented(`编辑字典项 ${row.label}`)">编辑</el-button>
        <el-button link type="primary" @click="notImplemented(`删除字典项 ${row.label}`)">删除</el-button>
      </template>
    </TablePage>
  </div>
</template>
