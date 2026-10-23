<script setup lang="ts">
/**
 * StandardUnitView —— 标准单耗（产线 × 能源类型）
 *
 * 这里维护的是**考核标准**，不是实测用量：
 *   · 电：kWh/千瓶，直接驱动大屏单耗对标与后台目标线；
 *   · 水 / 汽：m³/千瓶、t/千瓶，目前只有车间总表、没有分线实测，
 *     所以允许配置但界面标注「无分线实测」，不伪造成可计算的分线指标。
 *
 * ⚠ 空值 = 未标定，与填 0 完全不同：未标定的格子不画目标线，填 0 会得到
 *   「实际值远高于标准 0」的假结论。保存时留空即清空为未标定。
 */
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { adminApi, type StandardUnitMatrix, type StandardUnitRow } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';

const loading = ref(false);
const saving = ref(false);
const matrix = ref<StandardUnitMatrix | null>(null);
/** `${line}|${energy}` → 文本；空串表示未标定 */
const draft = reactive<Record<string, string>>({});

const tableRows = computed(() => {
  const m = matrix.value;
  if (!m) return [];
  return m.lines.map((line) => ({
    line,
    cells: Object.fromEntries(
      m.rows.filter((r) => r.line === line.code).map((r) => [r.energy, r])
    ) as Record<string, StandardUnitRow>
  }));
});

function key(line: string, energy: string): string {
  return `${line}|${energy}`;
}

function load() {
  loading.value = true;
  adminApi
    .getStandardUnits()
    .then((data) => {
      matrix.value = data;
      Object.keys(draft).forEach((k) => delete draft[k]);
      data.rows.forEach((r) => {
        draft[key(r.line, r.energy)] = r.value === null ? '' : String(r.value);
      });
    })
    .catch((e: Error) => ElMessage.error(e.message))
    .finally(() => {
      loading.value = false;
    });
}

function parseValue(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  return Number(t);
}

async function save() {
  const m = matrix.value;
  if (!m) return;
  const items: Pick<StandardUnitRow, 'line' | 'energy' | 'value' | 'remark'>[] = [];
  for (const line of m.lines) {
    for (const et of m.energies) {
      const k = key(line.code, et.code);
      const raw = draft[k] ?? '';
      const value = parseValue(raw);
      if (value !== null && (!Number.isFinite(value) || value < 0)) {
        ElMessage.warning(`${line.name} / ${et.name} 的标准单耗必须是非负数字或留空`);
        return;
      }
      items.push({
        line: line.code,
        energy: et.code,
        value,
        remark: m.rows.find((r) => r.line === line.code && r.energy === et.code)?.remark ?? ''
      });
    }
  }
  saving.value = true;
  try {
    await adminApi.saveStandardUnits(items);
    ElMessage.success('标准单耗已保存（留空 = 未标定）');
    load();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button :loading="loading" @click="load">重新加载</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存全部</el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="标准单耗是考核口径，不是实测值"
      description="按「产线 × 能源类型」维护，统一折算到千瓶。电的标准会驱动大屏单耗对标；水 / 汽目前只有车间总表，没有分线实测，允许配置但界面会标注。留空 = 未标定，不要填 0。"
    />

    <el-card shadow="never">
      <el-table v-loading="loading" :data="tableRows" border stripe>
        <el-table-column label="产线" min-width="180">
          <template #default="{ row }">
            <b>{{ row.line.name }}</b>
            <span class="code">{{ row.line.code }}</span>
          </template>
        </el-table-column>
        <el-table-column
          v-for="et in matrix?.energies ?? []"
          :key="et.code"
          :label="`${et.name}（${et.perUnit}）`"
          min-width="240"
        >
          <template #default="{ row }">
            <el-input
              v-model="draft[key(row.line.code, et.code)]"
              placeholder="未标定"
              clearable
              size="small"
            >
              <template #append>{{ et.unit }}/千瓶</template>
            </el-input>
            <div v-if="!row.cells[et.code]?.perLineAvailable" class="hint">
              无分线实测，仅保存标准值
            </div>
          </template>
        </el-table-column>
      </el-table>
      <div class="foot">
        <span>数值最多保留 4 位小数；保存后大屏/后台重新取数即可看到电标准。</span>
        <span v-if="matrix?.rows.some((r) => r.updatedBy)" class="foot__meta">
          最近更新：{{ matrix.rows.find((r) => r.updatedBy)?.updatedBy }}
        </span>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.mb-16 {
  margin-bottom: 16px;
}
.code {
  margin-left: 8px;
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}
.hint {
  margin-top: 4px;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}
.foot {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.foot__meta {
  color: var(--el-text-color-placeholder);
}
</style>
