<script setup lang="ts" generic="T extends object">
/**
 * TablePage —— 通用列表页骨架
 *
 * 统一了后台所有列表页的交互：关键词检索 + 自定义筛选 + 分页 + 加载态 + 空态。
 * 各页面只需声明「列」与「取数函数」，其余（分页、Loading、刷新）由本组件承担。
 *
 * 列定义支持两种渲染方式：
 *  1. formatter —— 纯文本格式化（返回值直接渲染）
 *  2. slot 名   —— 通过具名插槽自定义渲染，如 <template #col-status="{ row }">
 *
 * 说明：泛型约束用 `T extends object` 而非 `Record<string, unknown>`，
 * 因为 TS 不会给 interface 生成隐式索引签名，后者会导致领域类型无法直接传入。
 */
import { computed, onMounted, ref, shallowRef, watch } from 'vue';
import type { PageQuery, PageResult } from '@eep/shared';
import type { TableColumn } from './table-types';

const props = withDefaults(
  defineProps<{
    columns: TableColumn<T>[];
    /** 取数函数：接收分页 + 检索条件 */
    fetch: (query: PageQuery) => Promise<PageResult<T>>;
    /** 关键词检索的输入框占位；传空字符串则隐藏搜索框 */
    keywordPlaceholder?: string;
    /** 额外查询条件（如状态、类型），变化时自动回到第一页重新取数 */
    extraQuery?: Record<string, unknown>;
    pageSize?: number;
    /** 操作列宽度；为 0 或未传则不显示操作列 */
    opWidth?: number | string;
    /** 表格最大高度，超出滚动 */
    maxHeight?: number | string;
    rowKey?: string;
  }>(),
  {
    keywordPlaceholder: '输入关键词检索',
    pageSize: 10,
    opWidth: 0,
    rowKey: 'id'
  }
);

const loading = ref(false);
const rows = shallowRef<T[]>([]) as { value: T[] };
const total = ref(0);
const keyword = ref('');
const page = ref(1);
const size = ref(props.pageSize);

const query = computed<PageQuery>(() => ({
  page: page.value,
  pageSize: size.value,
  keyword: keyword.value.trim(),
  ...(props.extraQuery ?? {})
}));

/** 安全取值：把领域对象按字符串索引读取，供 formatter / 默认渲染使用 */
function cell(row: T, prop: string): unknown {
  return (row as Record<string, unknown>)[prop];
}

function cellText(row: T, col: TableColumn<T>): string {
  if (col.formatter) return col.formatter(row);
  const v = cell(row, col.prop);
  return v === null || v === undefined ? '--' : String(v);
}

async function load() {
  loading.value = true;
  try {
    const res = await props.fetch(query.value);
    rows.value = res.list;
    total.value = res.total;
  } catch (e) {
    console.error('[列表取数失败]', e);
    rows.value = [];
    total.value = 0;
  } finally {
    loading.value = false;
  }
}

function reload(resetPage = false) {
  if (resetPage) page.value = 1;
  void load();
}

function resetAll() {
  keyword.value = '';
  reload(true);
}

/* 额外条件变化时回到第一页重新取数 */
watch(
  () => JSON.stringify(props.extraQuery ?? {}),
  () => reload(true)
);

onMounted(load);

defineExpose({ reload, load });
</script>

<template>
  <div class="table-page">
    <!-- 筛选栏 -->
    <div class="filter-bar">
      <el-input
        v-if="keywordPlaceholder"
        v-model="keyword"
        :placeholder="keywordPlaceholder"
        clearable
        style="width: 240px"
        @keyup.enter="reload(true)"
        @clear="reload(true)"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>

      <slot name="filters" />

      <el-button type="primary" @click="reload(true)">查询</el-button>
      <el-button @click="resetAll">重置</el-button>

      <span class="filter-bar__spacer" />
      <slot name="toolbar" />
    </div>

    <!-- 数据表 -->
    <el-table v-loading="loading" :data="rows" :row-key="rowKey" :max-height="maxHeight" size="default">
      <template v-for="col in columns" :key="col.prop">
        <el-table-column
          :prop="col.prop"
          :label="col.label"
          :width="col.width"
          :min-width="col.minWidth"
          :align="col.align ?? (col.mono ? 'right' : 'left')"
          :show-overflow-tooltip="col.showOverflow !== false"
        >
          <template #default="{ row }">
            <slot v-if="col.slot" :name="col.slot" :row="(row as T)" />
            <span v-else-if="col.mono" class="t-mono">{{ cellText(row as T, col) }}</span>
            <template v-else>{{ cellText(row as T, col) }}</template>
          </template>
        </el-table-column>
      </template>

      <el-table-column v-if="opWidth" label="操作" :width="opWidth" fixed="right">
        <template #default="{ row }">
          <slot name="op" :row="(row as T)" />
        </template>
      </el-table-column>

      <template #empty>
        <el-empty description="暂无数据" :image-size="72" />
      </template>
    </el-table>

    <!-- 分页 -->
    <el-pagination
      v-model:current-page="page"
      v-model:page-size="size"
      :total="total"
      :page-sizes="[10, 20, 50, 100]"
      layout="total, sizes, prev, pager, next, jumper"
      background
      @current-change="load"
      @size-change="reload(true)"
    />
  </div>
</template>

<style scoped lang="scss">
.table-page {
  background: #ffffff;
  border: 1px solid $yl-border;
  border-radius: 4px;
  padding: 16px;
}

.filter-bar { margin-bottom: 12px; }
</style>
