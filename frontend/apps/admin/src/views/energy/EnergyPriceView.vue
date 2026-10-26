<script setup lang="ts">
/**
 * EnergyPriceView —— 能源配置管理 · 能源单价
 *
 * 只维护电价 / 水价 / 汽价三项；它们仍是 `system_param` 里的同一条真值来源，
 * 只是从「系统参数」页独立到本菜单。留空 = 未标定（与 0 不同）。
 */
import { onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { adminApi, type EnergyPriceRow } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';

const loading = ref(false);
const saving = ref(false);
const rows = ref<EnergyPriceRow[]>([]);
/** key → 文本；空串表示未标定 */
const draft = reactive<Record<string, string>>({});

function load() {
  loading.value = true;
  adminApi
    .getEnergyPrices()
    .then((data) => {
      rows.value = data.items;
      data.items.forEach((r) => {
        draft[r.key] = r.value === null ? '' : String(r.value);
      });
    })
    .catch((e: Error) => ElMessage.error(e.message))
    .finally(() => {
      loading.value = false;
    });
}

async function save() {
  const items: { key: string; value: string | null }[] = [];
  for (const r of rows.value) {
    const raw = (draft[r.key] ?? '').trim();
    if (raw === '') {
      items.push({ key: r.key, value: null });
      continue;
    }
    const num = Number(raw);
    if (!Number.isFinite(num) || num < 0) {
      ElMessage.warning(`${r.name}必须是非负数字或留空`);
      return;
    }
    items.push({ key: r.key, value: raw });
  }
  saving.value = true;
  try {
    await adminApi.saveEnergyPrices(items);
    ElMessage.success('能源单价已保存（留空 = 未标定）');
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
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="能源单价"
      description="电价 / 水价 / 汽价用于成本折算；留空 = 未标定，相关费用按「算不出」处理，不会按 0 计入。"
    />

    <el-card v-loading="loading" shadow="never">
      <el-form label-width="120px" style="max-width: 560px">
        <el-form-item v-for="r in rows" :key="r.key" :label="r.name">
          <el-input v-model="draft[r.key]" clearable placeholder="留空 = 未标定">
            <template #append>{{ r.unit }}</template>
          </el-input>
          <div v-if="r.updatedBy" class="hint">最近更新：{{ r.updatedBy }}</div>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.mb-16 {
  margin-bottom: 16px;
}
.hint {
  margin-top: 4px;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}
</style>
