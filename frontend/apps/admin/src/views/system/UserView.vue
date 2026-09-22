<script setup lang="ts">
/**
 * UserView —— 用户管理
 *
 * ⚠ 合规要求：正式环境登录必须走公司统一身份认证（SSO），不允许独立账号体系。
 *   本页的「新增用户 / 重置密码」在接入 SSO 后应改为「授权 / 撤权」语义。
 *
 * 本期为框架阶段：列表查询与分页已通，写操作留给后端接口。
 */
import { computed, ref } from 'vue';
import { adminApi, type SysUser } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const status = ref('');
const roleName = ref('');
const extraQuery = computed(() => ({
  ...(status.value ? { status: status.value } : {}),
  ...(roleName.value ? { roleName: roleName.value } : {})
}));

const dialogVisible = ref(false);
const current = ref<SysUser | null>(null);

const columns: TableColumn[] = [
  { prop: 'username', label: '用户名', width: 130 },
  { prop: 'nickname', label: '姓名', width: 110 },
  { prop: 'deptName', label: '所属组织', width: 140 },
  { prop: 'roleName', label: '角色', width: 130, slot: 'col-role' },
  { prop: 'phone', label: '手机号', width: 140, mono: true },
  { prop: 'email', label: '邮箱', minWidth: 200 },
  { prop: 'status', label: '状态', width: 90, slot: 'col-status' },
  { prop: 'lastLoginAt', label: '最近登录', width: 180, mono: true }
];

function openEdit(row: SysUser) {
  current.value = row;
  dialogVisible.value = true;
}

function openCreate() {
  current.value = null;
  dialogVisible.value = true;
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" plain @click="notImplemented('导出用户清单')">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>&nbsp;新增用户
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="mb-16"
      title="账号体系合规提示"
      description="按代码仓库管理规范，登录必须走统一身份认证（SSO），不允许独立账号体系。本页在接入 SSO 前仅用于权限映射维护。"
    />

    <TablePage
      :columns="columns"
      :fetch="adminApi.getUsers"
      :extra-query="extraQuery"
      keyword-placeholder="用户名 / 姓名 / 手机号"
      :op-width="180"
    >
      <template #filters>
        <el-select v-model="roleName" placeholder="全部角色" clearable style="width: 150px">
          <el-option label="超级管理员" value="超级管理员" />
          <el-option label="能源管理员" value="能源管理员" />
          <el-option label="产线操作员" value="产线操作员" />
          <el-option label="只读用户" value="只读用户" />
        </el-select>
        <el-select v-model="status" placeholder="全部状态" clearable style="width: 120px">
          <el-option label="启用" value="enabled" />
          <el-option label="停用" value="disabled" />
        </el-select>
      </template>

      <template #col-role="{ row }">
        <el-tag type="primary" size="small">{{ row.roleName }}</el-tag>
      </template>

      <template #col-status="{ row }">
        <el-tag :type="row.status === 'enabled' ? 'success' : 'info'" size="small">
          {{ row.status === 'enabled' ? '启用' : '停用' }}
        </el-tag>
      </template>

      <template #op="{ row }">
        <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
        <el-button link type="primary" @click="notImplemented(`重置 ${row.username} 的密码`)">重置密码</el-button>
      </template>
    </TablePage>

    <el-dialog v-model="dialogVisible" :title="current ? `编辑用户 · ${current.username}` : '新增用户'" width="520px">
      <el-form label-width="92px" label-position="right">
        <el-form-item label="用户名">
          <el-input :model-value="current?.username" placeholder="用于登录的唯一标识" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input :model-value="current?.nickname" />
        </el-form-item>
        <el-form-item label="所属组织">
          <el-input :model-value="current?.deptName" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select :model-value="current?.roleName" placeholder="请选择角色" style="width: 100%">
            <el-option label="超级管理员" value="超级管理员" />
            <el-option label="能源管理员" value="能源管理员" />
            <el-option label="产线操作员" value="产线操作员" />
            <el-option label="只读用户" value="只读用户" />
          </el-select>
        </el-form-item>
        <el-form-item label="手机号">
          <el-input :model-value="current?.phone" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input :model-value="current?.email" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="notImplemented('保存用户')">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
</style>
