<script setup lang="ts">
/**
 * ParamView —— 系统参数
 *
 * v1.4.0 起接真实读写：这里是**成本算料的唯一维护入口**
 * （电价 / 水价 / 汽价 / 箱规 / 每月标准变动费用），数据中心那张
 * 「单箱变动费用」图直接依赖这几项。
 *
 * ⚠ 「未标定」与「0」是两回事：清空参数值 = 未标定，相关指标如实留空；
 *   填 0 会让标准线贴在零上，读成「实际费用远高于标准 0.00 元/箱」的假结论。
 *   所以编辑弹窗里留空与填 0 是两个不同动作，不能混。
 */
import { reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { adminApi, type SysParam } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const columns: TableColumn<SysParam>[] = [
  { prop: 'group', label: '分组', width: 130, slot: 'col-group' },
  { prop: 'key', label: '参数键', minWidth: 220 },
  { prop: 'name', label: '参数名称', minWidth: 170 },
  { prop: 'valueText', label: '参数值', minWidth: 140, mono: true, slot: 'col-value' },
  { prop: 'unit', label: '单位', minWidth: 110, mono: true },
  { prop: 'remark', label: '说明', minWidth: 300 },
  { prop: 'updatedBy', label: '更新人', minWidth: 110 },
  { prop: 'id', label: '操作', width: 90, slot: 'col-op' }
];

const GROUP_TYPE: Record<string, 'primary' | 'success' | 'warning' | 'info'> = {
  成本参数: 'success',
  能耗阈值: 'primary',
  采集策略: 'info',
  数据质量: 'warning',
  大屏参数: 'info',
  运维策略: 'warning',
  告警通知: 'warning'
};

const dialog = reactive({
  visible: false,
  saving: false,
  row: null as SysParam | null,
  /** 文本态：空串代表「未标定」，与 '0' 不同 */
  value: '',
  remark: ''
});

function openEdit(row: SysParam) {
  dialog.row = row;
  dialog.value = row.value === null || row.value === undefined ? '' : String(Number(row.value));
  dialog.remark = row.remark;
  dialog.visible = true;
}

async function save() {
  if (!dialog.row) return;
  // 只校验「填了值」的情况：填了就必须是数字；留空是合法动作（= 未标定）
  if (dialog.value !== '' && !/^-?\d+(\.\d+)?$/.test(dialog.value.trim())) {
    ElMessage.error('参数值必须是数字，或留空表示未标定');
    return;
  }
  dialog.saving = true;
  try {
    await adminApi.saveParam(dialog.row.id, {
      value: (dialog.value.trim() === '' ? null : Number(dialog.value.trim())) as unknown as string,
      remark: dialog.remark
    });
    ElMessage.success(
      dialog.value.trim() === '' ? '已清空为「未标定」，相关指标将如实留空' : '参数已更新，看板与图表下一次查询即生效'
    );
    dialog.visible = false;
    tableRef.value?.reload();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    dialog.saving = false;
  }
}

const tableRef = ref<{ reload: (reset?: boolean) => void } | null>(null);
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="notImplemented('恢复默认参数')"><el-icon><RefreshLeft /></el-icon>&nbsp;恢复默认</el-button>
        <el-button type="primary" @click="notImplemented('新增参数键（请用 seed_params 命令登记）')">
          <el-icon><Plus /></el-icon>&nbsp;新增参数
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="mb-16"
      title="改动影响面"
      description="成本参数（电价 / 水价 / 汽价 / 箱规 / 标准变动费用）直接决定数据中心「单箱变动费用」那张图；阈值类参数会改变大屏与后台的告警判定。新增参数键请走 `python manage.py seed_params`，本页只改值与说明 —— 键是代码里的取值凭据，改键等于把参数删掉。"
    />

    <TablePage
      ref="tableRef"
      :columns="columns"
      :fetch="adminApi.getParams"
      keyword-placeholder="参数键 / 名称 / 说明"
      :page-size="20"
    >
      <template #col-group="{ row }">
        <el-tag :type="GROUP_TYPE[row.group] ?? 'info'" size="small">{{ row.group }}</el-tag>
      </template>
      <template #col-value="{ row }">
        <!-- 单位有独立一列，这里不再重复带一次 -->
        <span :class="{ 'is-unset': row.value === null || row.value === undefined }">
          {{ row.valueText ?? (row.value === null ? '未标定' : row.value) }}
        </span>
      </template>
      <template #col-op="{ row }">
        <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
      </template>
    </TablePage>

    <el-dialog v-model="dialog.visible" title="修改系统参数" width="520px">
      <el-form v-if="dialog.row" label-width="88px">
        <el-form-item label="参数键">
          <span class="t-mono">{{ dialog.row.key }}</span>
        </el-form-item>
        <el-form-item label="参数名称">{{ dialog.row.name }}</el-form-item>
        <el-form-item label="参数值">
          <el-input v-model="dialog.value" clearable :placeholder="`留空 = 未标定（单位 ${dialog.row.unit || '—'}）`" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="dialog.remark" type="textarea" :rows="2" maxlength="200" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <span class="dialog-hint">⚠ 留空与填 0 含义不同：留空是「没标定」，0 是「标定为零」</span>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
.is-unset {
  color: var(--el-text-color-placeholder);
}
.dialog-hint {
  float: left;
  font-size: 12px;
  color: var(--el-color-warning);
}
</style>
