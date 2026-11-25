<script setup lang="ts">
/**
 * PlcReadingQueryView —— PLC 设备历史读数（v1.8.3）
 *
 * 参考仪表读数查询：看 `plc_reading` 的**原始读数**，而不是加工后的标箱产量。
 * 小时差值按「本小时最后一条 − 上一小时最后一条成功读数」计算（v1.8.4 起
 * PLC 与能源一样每小时采一次）：
 *   · 上一小时没有成功读数 → 「数据不足」，不拿 0 冒充；
 *   · 累计值回退（换表 / 归零）→ 差值不成立；
 *   · 规格点不计算差值，只显示现场当前选择。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import {
  adminApi,
  type PlcDevice,
  type PlcPoint,
  type PlcReadingQuery,
  type PlcReadingRow
} from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { hourAgo } from '@/utils/energyUsage';

/** 包含当前小时：后端 to 是「整点且含等号」，用下一个整点才不会漏掉本小时读数 */
function nextHourText(): string {
  const d = new Date();
  d.setHours(d.getHours() + 1, 0, 0, 0);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:00`;
}

const range = ref<[string, string]>([hourAgo(24), nextHourText()]);
const deviceId = ref<number | ''>('');
const pointId = ref<number | ''>('');
const onlyFail = ref(false);
const loadingMeta = ref(true);
const devices = ref<PlcDevice[]>([]);
const points = ref<PlcPoint[]>([]);

onMounted(async () => {
  try {
    devices.value = await adminApi.getPlcDevices();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    loadingMeta.value = false;
  }
});

const pointOptions = computed(() =>
  deviceId.value ? points.value.filter((p) => p.deviceId === deviceId.value) : points.value
);

watch(deviceId, async (id) => {
  pointId.value = '';
  if (!id) {
    points.value = [];
    return;
  }
  try {
    points.value = await adminApi.getPlcPoints(id);
  } catch (e) {
    ElMessage.error((e as Error).message);
    points.value = [];
  }
});

const QUICK = [
  { label: '近 6 小时', hours: 6 },
  { label: '近 24 小时', hours: 24 },
  { label: '近 3 日', hours: 72 }
];

function applyQuick(hours: number) {
  range.value = [hourAgo(hours), nextHourText()];
}

const tableRef = ref<{ reload: (reset?: boolean) => void } | null>(null);

async function fetchReadings(query: { page?: number; pageSize?: number; keyword?: string }) {
  const q: PlcReadingQuery = {
    from: range.value[0],
    to: range.value[1],
    page: query.page ?? 1,
    pageSize: query.pageSize ?? 50
  };
  if (deviceId.value) q.device = deviceId.value;
  if (pointId.value) q.point = pointId.value;
  if (onlyFail.value) q.ok = 0;
  return adminApi.getPlcReadings(q);
}

const columns: TableColumn<PlcReadingRow>[] = [
  { prop: 'collectTime', label: '采集时间', minWidth: 165, mono: true },
  { prop: 'deviceName', label: 'PLC 设备', minWidth: 150 },
  { prop: 'point', label: '点位编码', minWidth: 120, mono: true },
  { prop: 'pointName', label: '点位名称', minWidth: 150 },
  { prop: 'roleLabel', label: '用途', minWidth: 130 },
  { prop: 'address', label: '地址', minWidth: 100, mono: true },
  { prop: 'value', label: '原始读数', minWidth: 140, mono: true, slot: 'col-value' },
  { prop: 'unit', label: '单位', minWidth: 70 },
  { prop: 'hourDelta', label: '小时差值', minWidth: 150, mono: true, slot: 'col-delta' },
  { prop: 'ok', label: '状态', minWidth: 90, slot: 'col-ok' },
  { prop: 'error', label: '错误', minWidth: 200, showOverflow: true }
];

function valueText(v: number | null): string {
  return v === null || v === undefined ? '--' : v.toLocaleString('zh-CN', { maximumFractionDigits: 3 });
}

function deltaText(row: PlcReadingRow): string {
  if (row.hourDelta === null || row.hourDelta === undefined) return '--';
  return row.hourDelta.toLocaleString('zh-CN', { maximumFractionDigits: 3 });
}

function deltaStatus(row: PlcReadingRow): { label: string; type: 'success' | 'info' | 'warning' | 'danger' } | null {
  if (row.hourStatus === 'ok') return { label: '正常', type: 'success' };
  if (row.hourStatus === 'spec') return { label: '规格点', type: 'info' };
  if (row.hourStatus === 'reset') return { label: '回退', type: 'danger' };
  if (row.hourStatus === 'insufficient') return { label: '数据不足', type: 'warning' };
  return null;
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button :loading="loadingMeta" @click="tableRef?.reload(true)">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="这里看的是 PLC 原始读数，不是标箱产量"
      description="小时差值 = 本小时最后一条 − 上一小时最后一条成功读数；上一小时没有成功读数会标「数据不足」，规格点不计算差值。"
    />

    <el-card shadow="never" class="mb-16">
      <div class="query-row">
        <span class="query-label">时间</span>
        <el-date-picker
          v-model="range"
          type="datetimerange"
          value-format="YYYY-MM-DD HH:mm"
          format="YYYY-MM-DD HH:mm"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          :clearable="false"
          style="width: 380px"
        />
        <span class="query-label">PLC 设备</span>
        <el-select v-model="deviceId" size="small" clearable placeholder="全部" style="width: 190px">
          <el-option v-for="d in devices" :key="d.id" :label="`${d.name}（${d.code}）`" :value="d.id" />
        </el-select>
        <span class="query-label">点位</span>
        <el-select
          v-model="pointId"
          size="small"
          clearable
          :disabled="!deviceId"
          placeholder="全部点位"
          style="width: 210px"
        >
          <el-option v-for="p in pointOptions" :key="p.id" :label="`${p.name}（${p.code}）`" :value="p.id" />
        </el-select>
        <el-checkbox v-model="onlyFail" size="small">只看失败</el-checkbox>
        <el-button-group>
          <el-button v-for="q in QUICK" :key="q.label" size="small" @click="applyQuick(q.hours)">
            {{ q.label }}
          </el-button>
        </el-button-group>
        <el-button type="primary" size="small" @click="tableRef?.reload(true)">查询</el-button>
      </div>
    </el-card>

    <TablePage
      ref="tableRef"
      :columns="columns"
      :fetch="fetchReadings"
      :page-size="50"
      row-key="id"
      empty-text="所选时间内没有 PLC 原始读数"
    >
      <template #col-value="{ row }">
        <span class="mono">{{ valueText(row.value) }}</span>
      </template>
      <template #col-delta="{ row }">
        <span class="mono">{{ deltaText(row) }}</span>
        <el-tag v-if="deltaStatus(row)" size="small" :type="deltaStatus(row)!.type" class="ml-6">
          {{ deltaStatus(row)!.label }}
        </el-tag>
      </template>
      <template #col-ok="{ row }">
        <el-tag size="small" :type="row.ok ? 'success' : 'danger'">{{ row.ok ? '成功' : '失败' }}</el-tag>
      </template>
    </TablePage>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
.ml-6 { margin-left: 6px; }
.query-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.query-label {
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.mono {
  font-family: monospace;
}
</style>
