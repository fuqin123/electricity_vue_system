<script setup lang="ts">
/**
 * MenuView —— 菜单管理
 * 菜单树结构、路由路径与可见性。菜单与路由必须一一对应，
 * 改菜单时同步改 router/index.ts，否则会出现「点了 404」。
 */
import { computed, onMounted, ref } from 'vue';
import { adminApi, type SysMenu } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import { notImplemented } from '@/utils/pending';

const menus = ref<SysMenu[]>([]);
const loading = ref(true);
const keyword = ref('');

onMounted(async () => {
  try {
    menus.value = await adminApi.getMenus();
  } finally {
    loading.value = false;
  }
});

interface TreeNode extends SysMenu {
  children?: TreeNode[];
}

/** 组装菜单树 */
const tree = computed<TreeNode[]>(() => {
  const map = new Map<number, TreeNode>();
  menus.value.forEach((m) => map.set(m.id, { ...m }));
  const roots: TreeNode[] = [];
  map.forEach((node) => {
    if (node.parentId === 0) roots.push(node);
    else {
      const parent = map.get(node.parentId);
      if (parent) (parent.children ??= []).push(node);
      else roots.push(node);
    }
  });
  const kw = keyword.value.trim().toLowerCase();
  if (!kw) return roots;
  const filter = (nodes: TreeNode[]): TreeNode[] =>
    nodes
      .map((n) => ({ ...n, children: n.children ? filter(n.children) : undefined }))
      .filter((n) => n.name.toLowerCase().includes(kw) || n.path.toLowerCase().includes(kw) || (n.children?.length ?? 0) > 0);
  return filter(roots);
});

const TYPE_MAP: Record<string, { text: string; type: 'primary' | 'success' | 'info' }> = {
  catalog: { text: '目录', type: 'info' },
  menu: { text: '菜单', type: 'primary' },
  button: { text: '按钮', type: 'success' }
};
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="notImplemented('展开 / 折叠全部')"><el-icon><Sort /></el-icon>&nbsp;展开全部</el-button>
        <el-button type="primary" @click="notImplemented('新增菜单')"><el-icon><Plus /></el-icon>&nbsp;新增菜单</el-button>
      </template>
    </PageHeader>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="mb-16"
      title="菜单与路由一致性"
      description="菜单的 path 必须与前端路由表（apps/admin/src/router/index.ts）中的路径完全一致，改菜单时务必同步改路由，否则会出现「点击跳 404」。"
    />

    <div class="card">
      <div class="card__head">
        <h3 class="card__title">菜单树</h3>
        <el-input v-model="keyword" placeholder="搜索菜单名称 / 路径" clearable style="width: 240px" />
      </div>

      <el-table
        v-loading="loading"
        :data="tree"
        row-key="id"
        default-expand-all
        :tree-props="{ children: 'children' }"
        size="default"
      >
        <el-table-column prop="name" label="菜单名称" min-width="200" />
        <el-table-column prop="path" label="路由路径" min-width="200">
          <template #default="{ row }"><span class="t-mono">{{ row.path }}</span></template>
        </el-table-column>
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag :type="TYPE_MAP[row.type]?.type ?? 'info'" size="small">{{ TYPE_MAP[row.type]?.text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="icon" label="图标标识" width="130">
          <template #default="{ row }"><span class="t-mono t-subtle">{{ row.icon }}</span></template>
        </el-table-column>
        <el-table-column prop="sort" label="排序" width="80" align="right" />
        <el-table-column label="可见" width="80">
          <template #default="{ row }">
            <el-tag :type="row.visible ? 'success' : 'info'" size="small">{{ row.visible ? '是' : '否' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="notImplemented(`编辑菜单 ${row.name}`)">编辑</el-button>
            <el-button link type="primary" @click="notImplemented(`新增子菜单 · ${row.name}`)">加子项</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
</style>
