<script setup lang="ts">
/**
 * StandardUnitView —— 能源配置管理 · 标准单耗配置
 *
 * 一行 = 产线 × 瓶型容量，电/水/汽三列。
 * `value = null` 或 0 在界面显示 `-`：0 不能画成一条贴在零轴上的目标线。
 *
 * 水 / 汽目前只有车间总表、没有分线实测；这里仍允许配置（为后续分线计量留位置），
 * 页面会明确标注，不把配置值伪装成可计算的分线实际值。
 */
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  adminApi,
  type StandardUnitLineOption,
  type StandardUnitOptions,
  type StandardUnitRow
} from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';

const options = ref<StandardUnitOptions>({ workshops: [], lines: [], energies: [], rows: [] });
const rows = ref<StandardUnitRow[]>([]);
const loading = ref(false);
const saving = ref(false);

const filters = reactive({ workshop: '', line: '', capacity: '' });

const lineOptions = computed(() =>
  filters.workshop
    ? options.value.lines.filter((l) => l.workshopCode === filters.workshop)
    : options.value.lines
);

function onWorkshopChange() {
  filters.line = '';
}

function load() {
  loading.value = true;
  adminApi
    .getStandardUnits({
      workshop: filters.workshop || undefined,
      line: filters.line || undefined,
      capacity: filters.capacity.trim() || undefined
    })
    .then((data) => {
      options.value = data;
      rows.value = data.rows;
    })
    .catch((e: Error) => ElMessage.error(e.message))
    .finally(() => {
      loading.value = false;
    });
}

function resetFilters() {
  filters.workshop = '';
  filters.line = '';
  filters.capacity = '';
  load();
}

/** 0 与 null 都显示 -；其余按最多 4 位小数显示，避免 8.5000 这种噪声 */
function formatStd(value: number | null | undefined): string {
  if (value === null || value === undefined || Number(value) === 0) return '-';
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 4 });
}

/* ── 新增 / 修改 ───────────────────────────────────────── */
const dialog = reactive({
  visible: false,
  adding: true,
  line: '',
  bottleCapacity: '',
  values: { ELECTRIC: '', WATER: '', STEAM: '' } as Record<string, string>,
  remark: ''
});

const dialogLine = computed<StandardUnitLineOption | undefined>(() =>
  options.value.lines.find((l) => l.code === dialog.line)
);

function onDialogLineChange() {
  if (dialog.adding) dialog.bottleCapacity = dialogLine.value?.capacity ?? '';
}

function openAdd() {
  dialog.adding = true;
  dialog.line = lineOptions.value[0]?.code ?? options.value.lines[0]?.code ?? '';
  dialog.bottleCapacity = dialogLine.value?.capacity ?? '';
  dialog.values = { ELECTRIC: '', WATER: '', STEAM: '' };
  dialog.remark = '';
  dialog.visible = true;
}

function openEdit(row: StandardUnitRow) {
  dialog.adding = false;
  dialog.line = row.line;
  dialog.bottleCapacity = row.bottleCapacity;
  dialog.values = {
    ELECTRIC: row.values.ELECTRIC === null ? '' : String(row.values.ELECTRIC),
    WATER: row.values.WATER === null ? '' : String(row.values.WATER),
    STEAM: row.values.STEAM === null ? '' : String(row.values.STEAM)
  };
  dialog.remark = row.remark;
  dialog.visible = true;
}

async function save() {
  if (!dialog.line) {
    ElMessage.warning('请选择产线');
    return;
  }
  if (!dialog.bottleCapacity.trim()) {
    ElMessage.warning('请填写瓶型容量，如 500ml');
    return;
  }
  const values: Record<string, string | null> = {};
  for (const code of ['ELECTRIC', 'WATER', 'STEAM']) {
    const raw = (dialog.values[code] ?? '').trim();
    if (raw === '') {
      values[code] = null;
      continue;
    }
    const num = Number(raw);
    if (!Number.isFinite(num) || num < 0) {
      ElMessage.warning(`标准单耗必须是非负数字或留空（${code}）`);
      return;
    }
    values[code] = raw;
  }
  saving.value = true;
  try {
    await adminApi.saveStandardUnit({
      line: dialog.line,
      bottleCapacity: dialog.bottleCapacity.trim(),
      values,
      remark: dialog.remark
    });
    ElMessage.success(dialog.adding ? '已新增标准单耗' : '已保存修改');
    dialog.visible = false;
    load();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    saving.value = false;
  }
}

async function remove(row: StandardUnitRow) {
  try {
    await ElMessageBox.confirm(
      `确定删除「${row.workshop || '—'} · ${row.lineName} · ${row.bottleCapacity || '未填瓶型'}」的标准单耗吗？`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    );
  } catch {
    return;
  }
  try {
    await adminApi.deleteStandardUnit(row.line, row.bottleCapacity);
    ElMessage.success('已删除');
    load();
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

onMounted(load);
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" @click="openAdd">新增</el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="标准单耗配置"
      description="一行对应「产线 + 瓶型容量」。单耗为 0 或未配置时显示 -，不会画成 0 目标线。水/汽目前只有车间总表，分线标准仅作为后续分线计量配置，不代表已有分线实测。"
    />

    <el-card shadow="never">
      <div class="filters">
        <span class="label">车间</span>
        <el-select
          v-model="filters.workshop"
          clearable
          placeholder="全部车间"
          style="width: 180px"
          @change="onWorkshopChange"
        >
          <el-option v-for="w in options.workshops" :key="w.code" :label="w.name" :value="w.code" />
        </el-select>

        <span class="label">产线</span>
        <el-select v-model="filters.line" clearable placeholder="全部产线" style="width: 200px">
          <el-option
            v-for="l in lineOptions"
            :key="l.code"
            :label="`${l.code} ${l.name}`"
            :value="l.code"
          />
        </el-select>

        <span class="label">瓶型容量</span>
        <el-input
          v-model="filters.capacity"
          clearable
          placeholder="如 500ml"
          style="width: 150px"
          @keyup.enter="load"
        />

        <el-button type="primary" :loading="loading" @click="load">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </div>

      <el-table v-loading="loading" :data="rows" border stripe empty-text="暂无标准单耗配置，点击右上角「新增」">
        <el-table-column label="序号" width="70" align="center">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column prop="workshop" label="车间名" min-width="130" />
        <el-table-column prop="line" label="产线编码" min-width="110" />
        <el-table-column prop="lineName" label="产线名称" min-width="120" />
        <el-table-column prop="bottleCapacity" label="瓶型容量" min-width="110">
          <template #default="{ row }">{{ row.bottleCapacity || '-' }}</template>
        </el-table-column>
        <el-table-column label="电标准单耗" min-width="120" align="right">
          <template #default="{ row }">{{ formatStd(row.values.ELECTRIC) }}</template>
        </el-table-column>
        <el-table-column label="水标准单耗" min-width="120" align="right">
          <template #default="{ row }">{{ formatStd(row.values.WATER) }}</template>
        </el-table-column>
        <el-table-column label="汽标准单耗" min-width="120" align="right">
          <template #default="{ row }">{{ formatStd(row.values.STEAM) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">修改</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="dialog.visible"
      :title="dialog.adding ? '新增标准单耗' : '修改标准单耗'"
      width="560px"
    >
      <el-form label-width="100px">
        <el-form-item label="产线">
          <el-select
            v-model="dialog.line"
            filterable
            :disabled="!dialog.adding"
            placeholder="选择产线"
            style="width: 100%"
            @change="onDialogLineChange"
          >
            <el-option
              v-for="l in options.lines"
              :key="l.code"
              :label="`${l.workshop ? l.workshop + ' · ' : ''}${l.code} ${l.name}`"
              :value="l.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="瓶型容量" required>
          <el-input
            v-model="dialog.bottleCapacity"
            :disabled="!dialog.adding"
            placeholder="如 500ml / 550ml / 1L"
          />
        </el-form-item>
        <el-form-item label="电标准单耗">
          <el-input v-model="dialog.values.ELECTRIC" placeholder="留空 = 未配置" clearable>
            <template #append>kWh/千瓶</template>
          </el-input>
        </el-form-item>
        <el-form-item label="水标准单耗">
          <el-input v-model="dialog.values.WATER" placeholder="留空 = 未配置" clearable>
            <template #append>m³/千瓶</template>
          </el-input>
        </el-form-item>
        <el-form-item label="汽标准单耗">
          <el-input v-model="dialog.values.STEAM" placeholder="留空 = 未配置" clearable>
            <template #append>t/千瓶</template>
          </el-input>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="dialog.remark" maxlength="200" show-word-limit placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.mb-16 {
  margin-bottom: 16px;
}
.filters {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
