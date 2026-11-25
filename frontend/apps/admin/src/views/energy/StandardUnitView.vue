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
  type StandardCaseRule,
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

/* ── 标箱换算规则（v1.8.1）──────────────────────────────── */
const caseRules = ref<StandardCaseRule[]>([]);
const caseLoading = ref(false);
const caseSaving = ref(false);
const caseDialog = reactive({
  visible: false,
  editingId: null as number | null,
  inCount: 12,
  ml: 500,
  factor: 1,
  remark: ''
});

function loadCaseRules() {
  caseLoading.value = true;
  adminApi
    .getStandardCaseRules()
    .then((rows) => {
      caseRules.value = rows;
    })
    .catch((e: Error) => ElMessage.error(e.message))
    .finally(() => {
      caseLoading.value = false;
    });
}

function openCaseAdd() {
  Object.assign(caseDialog, { visible: true, editingId: null, inCount: 12, ml: 500, factor: 1, remark: '' });
}

function openCaseEdit(row: StandardCaseRule) {
  Object.assign(caseDialog, {
    visible: true,
    editingId: row.id,
    inCount: row.inCount,
    ml: row.ml,
    factor: row.factor,
    remark: row.remark
  });
}

async function saveCaseRule() {
  if (!caseDialog.inCount || !caseDialog.ml || !caseDialog.factor) {
    ElMessage.warning('入数、ml、设定值都必须大于 0');
    return;
  }
  caseSaving.value = true;
  try {
    const payload = {
      inCount: caseDialog.inCount,
      ml: caseDialog.ml,
      factor: caseDialog.factor,
      remark: caseDialog.remark
    };
    if (caseDialog.editingId) {
      await adminApi.updateStandardCaseRule(caseDialog.editingId, payload);
      ElMessage.success('标箱换算规则已保存');
    } else {
      await adminApi.createStandardCaseRule(payload);
      ElMessage.success('标箱换算规则已新增');
    }
    caseDialog.visible = false;
    loadCaseRules();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    caseSaving.value = false;
  }
}

async function removeCaseRule(row: StandardCaseRule) {
  try {
    await ElMessageBox.confirm(
      `删除「${row.inCount}入 / ${row.ml}ml → ${row.factor}」的标箱换算规则？`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    );
  } catch {
    return;
  }
  try {
    await adminApi.deleteStandardCaseRule(row.id);
    ElMessage.success('已删除');
    loadCaseRules();
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

onMounted(() => {
  load();
  loadCaseRules();
});
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

    <!-- 标箱换算（v1.8.1）：01 电眼瓶数 / 02 电眼箱数 → 标箱 -->
    <el-card shadow="never" class="mt-16">
      <template #header>
        <div class="card-head">
          <div>
            <b>标箱换算</b>
            <span class="card-head__note">
              标箱数 = 箱数 × 设定值（四舍五入）；实际入数 = 01 电眼瓶数 ÷ 02 电眼箱数
            </span>
          </div>
          <el-button size="small" type="primary" @click="openCaseAdd">新增规则</el-button>
        </div>
      </template>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="mb-12"
        title="示例：12 入（瓶/箱）+ 规格 330 → 设定值 1.25，标箱数 = 箱数 × 1.25"
        description="PLC 会读取现场选择的 ml 数；未配置对应规则时按 1.0 计，并在产量记录里标记「未命中规则」。"
      />
      <el-table v-loading="caseLoading" :data="caseRules" border stripe empty-text="暂无标箱换算规则">
        <el-table-column prop="inCount" label="入数（瓶/箱）" width="140" align="right" />
        <el-table-column prop="ml" label="规格" width="120" align="right" />
        <el-table-column prop="factor" label="设定值" width="140" align="right" />
        <el-table-column label="说明" min-width="200">
          <template #default="{ row }">{{ row.remark || '—' }}</template>
        </el-table-column>
        <el-table-column prop="updatedAt" label="更新时间" width="170" />
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openCaseEdit(row)">修改</el-button>
            <el-button link type="danger" @click="removeCaseRule(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="caseDialog.visible" :title="caseDialog.editingId ? '修改标箱换算' : '新增标箱换算'" width="460px">
      <el-form label-width="120px">
        <el-form-item label="入数（瓶/箱）" required>
          <el-input-number v-model="caseDialog.inCount" :min="1" :max="999" controls-position="right" />
        </el-form-item>
        <el-form-item label="规格" required>
          <el-input-number v-model="caseDialog.ml" :min="1" :max="100000" controls-position="right" />
        </el-form-item>
        <el-form-item label="设定值" required>
          <el-input-number v-model="caseDialog.factor" :min="0.0001" :step="0.05" :precision="4" controls-position="right" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="caseDialog.remark" maxlength="200" show-word-limit placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="caseDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="caseSaving" @click="saveCaseRule">保存</el-button>
      </template>
    </el-dialog>

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
.mb-12 {
  margin-bottom: 12px;
}
.mt-16 {
  margin-top: 16px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.card-head__note {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
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
