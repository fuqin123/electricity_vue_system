<script setup lang="ts">
/**
 * RoleView —— 角色管理
 * 角色定义与菜单 / 按钮权限分配。角色编码需与后端权限中间件保持一致。
 */
import { adminApi } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const columns: TableColumn[] = [
  { prop: 'code', label: '角色编码', width: 160 },
  { prop: 'name', label: '角色名称', width: 150 },
  { prop: 'userCount', label: '绑定用户数', width: 120, mono: true },
  { prop: 'remark', label: '权限说明', minWidth: 280 },
  { prop: 'createdAt', label: '创建时间', width: 180, mono: true }
];
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" @click="notImplemented('新增角色')">
          <el-icon><Plus /></el-icon>&nbsp;新增角色
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="权限模型"
      description="采用「角色 → 菜单 / 按钮」两级授权。所有接口必须鉴权，白名单接口需单独评审通过后方可放行。"
    />

    <TablePage :columns="columns" :fetch="adminApi.getRoles" keyword-placeholder="角色编码 / 名称" :op-width="200">
      <template #op="{ row }">
        <el-button link type="primary" @click="notImplemented(`分配权限 · ${row.name}`)">分配权限</el-button>
        <el-button link type="primary" @click="notImplemented(`编辑角色 ${row.name}`)">编辑</el-button>
        <el-button link type="primary" :disabled="row.code === 'super_admin'" @click="notImplemented(`删除角色 ${row.name}`)">
          删除
        </el-button>
      </template>
    </TablePage>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
</style>
