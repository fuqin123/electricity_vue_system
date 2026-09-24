<script setup lang="ts">
/**
 * ReadingQueryView —— 仪表读数查询（设备与采集）
 *
 * 查的是 `energy_raw` 的**原始累计读数**，不是用量：
 *   · `/api/energy/hourly` 给的是差分后的用量，倍率、字序、原始报文都被加工掉了；
 *   · 现场排查「这块表读到的数对不对、倍率有没有错、什么时候开始断讯」必须看读数本身。
 *
 * 三级筛选：时间（精确到小时）→ 网关 → 仪表。选仪表时网关自动跟随，
 * 因为仪表从属于网关，两个下拉同时独立选会出现「网关 A + 网关 B 的表」这种空结果。
 *
 * ⚠ 采集失败的行 `reading` 是 **null**（界面显示 `--`），不是 0 ——
 *   0 会被差值当成真实读数，进而算出一个假的用量（B-502 的源头就在这张表）。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { adminApi, type Gateway, type MeterPoint, type MeterReading, type ReadingQuery } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { hourAgo, nowHourText } from '@/utils/energyUsage';
import { notImplemented } from '@/utils/pending';

/* ── 筛选项 ───────────────────────────────────────────── */
const range = ref<[string, string]>([hourAgo(6), nowHourText()]);
const gatewayId = ref<number | ''>('');
const meterId = ref<number | ''>('');
const energy = ref('');
const onlyFail = ref(false);
const loadingMeta = ref(true);
const gateways = ref<Gateway[]>([]);
const meters = ref<MeterPoint[]>([]);

onMounted(async () => {
  try {
    const [gw, mt] = await Promise.all([adminApi.getGateways(), adminApi.getMeters({ page: 1, pageSize: 200 })]);
    gateways.value = gw;
    meters.value = mt.list;
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    loadingMeta.value = false;
  }
});

/** 网关选定后，仪表下拉只留该网关下的表 */
const meterOptions = computed(() =>
  gatewayId.value ? meters.value.filter((m) => m.gatewayId === gatewayId.value) : meters.value
);

/* 选了仪表就把网关对齐到它所属网关，避免出现「网关 A + 表 B」的空结果 */
watch(meterId, (id) => {
  if (!id) return;
  const m = meters.value.find((x) => x.id === id);
  if (m && gatewayId.value !== m.gatewayId) gatewayId.value = m.gatewayId;
});
watch(gatewayId, (g) => {
  const m = meters.value.find((x) => x.id === meterId.value);
  if (m && m.gatewayId !== g) meterId.value = '';
});

const QUICK = [
  { label: '近 6 小时', hours: 6 },
  { label: '今日', hours: 0 },
  { label: '近 3 日', hours: 72 }
];

function applyQuick(hours: number) {
  range.value = [hourAgo(hours), nowHourText()];
}

/* ── 取数 ─────────────────────────────────────────────── */
/** TablePage 是泛型函数组件，不能 InstanceType<>，只声明这里要用的方法 */
const tableRef = ref<{ reload: (reset?: boolean) => void } | null>(null);
const lastPage = ref<MeterReading[]>([]);

async function fetchReadings(query: { page?: number; pageSize?: number; keyword?: string }) {
  const q: ReadingQuery = {
    from: range.value[0],
    to: range.value[1],
    page: query.page ?? 1,
    pageSize: query.pageSize ?? 50
  };
  if (gatewayId.value) q.gateway = gatewayId.value;
  if (meterId.value) q.meter = meterId.value;
  if (energy.value) q.energy = energy.value;
  if (onlyFail.value) q.ok = 0;
  const res = await adminApi.getReadings(q);
  lastPage.value = res.list;
  return res;
}

const columns: TableColumn<MeterReading>[] = [
  { prop: 'collectTime', label: '采集时间', minWidth: 165, mono: true },
  { prop: 'gateway', label: '网关', minWidth: 150 },
  { prop: 'meter', label: '仪表编码', minWidth: 120, mono: true },
  { prop: 'meterName', label: '仪表名称', minWidth: 150 },
  { prop: 'lineName', label: '归属', minWidth: 100 },
  { prop: 'energyName', label: '能源', minWidth: 80 },
  { prop: 'reading', label: '累计读数', minWidth: 140, mono: true, slot: 'col-reading' },
  { prop: 'unit', label: '单位', minWidth: 70 },
  { prop: 'ok', label: '状态', minWidth: 96, slot: 'col-ok' },
  { prop: 'raw', label: '原始报文 / 响应', minWidth: 220, showOverflow: false, slot: 'col-raw' }
];

function readingText(v: number | null): string {
  // null = 这次没读到。显示 -- 而不是 0：0 会被当成真实读数参与差分（B-502）
  return v === null || v === undefined ? '--' : v.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
}

/* ── 导出 ─────────────────────────────────────────────── */
function exportCsv() {
  if (!lastPage.value.length) {
    ElMessage.warning('当前页没有可导出的数据');
    return;
  }
  const head = ['采集时间', '网关', '仪表编码', '仪表名称', '归属', '能源', '累计读数', '单位', '状态', '原始报文', '错误'];
  const lines = [head.join(',')];
  lastPage.value.forEach((r) => {
    lines.push(
      [
        r.collectTime,
        r.gateway,
        r.meter,
        r.meterName,
        r.lineName,
        r.energyName,
        r.reading === null ? '' : String(r.reading),
        r.unit,
        r.ok ? '成功' : '失败',
        `"${String(r.raw).replace(/"/g, '""')}"`,
        `"${String(r.error).replace(/"/g, '""')}"`
      ].join(',')
    );
  });
  // ⚠ 必须加 UTF-8 BOM，否则 Excel 打开中文表头会乱码
  const csv = '\ufeff' + lines.join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `仪表读数_${range.value[0].replace(/[^0-9]/g, '')}_${range.value[1].replace(/[^0-9]/g, '')}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  ElMessage.success('已导出当前页（读数为空的行是采集失败，不是 0）');
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" plain @click="notImplemented('导出全部读数')">
          <el-icon><Download /></el-icon>&nbsp;导出全部
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="这里看的是原始累计读数"
      description="与「能源计量 → 用量查询」的分工：那边是差分后的用量，这边是读数本身。排查倍率、字序、断讯从这页开始。采集失败的行读数为空（--），不会写 0。"
    />

    <el-card shadow="never" class="mb-16">
      <div class="query">
        <div class="query__row">
          <span class="query__label">起止时间</span>
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
          <el-button-group>
            <el-button v-for="q in QUICK" :key="q.label" size="small" @click="applyQuick(q.hours)">{{ q.label }}</el-button>
          </el-button-group>
        </div>

        <div class="query__row">
          <span class="query__label">网关</span>
          <el-select v-model="gatewayId" size="small" style="width: 200px" :loading="loadingMeta" clearable placeholder="全部网关">
            <el-option v-for="g in gateways" :key="g.id" :label="`${g.code} ${g.name}`" :value="g.id" />
          </el-select>

          <span class="query__label">仪表</span>
          <el-select
            v-model="meterId"
            size="small"
            style="width: 240px"
            filterable
            clearable
            :loading="loadingMeta"
            placeholder="全部仪表（可输入编码搜索）"
          >
            <el-option v-for="m in meterOptions" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" />
          </el-select>

          <span class="query__label">能源</span>
          <el-select v-model="energy" size="small" style="width: 130px" clearable placeholder="全部">
            <el-option label="电" value="ELECTRIC" />
            <el-option label="水" value="WATER" />
            <el-option label="汽" value="STEAM" />
          </el-select>

          <el-checkbox v-model="onlyFail" size="small">只看失败</el-checkbox>

          <el-button type="primary" size="small" @click="tableRef?.reload(true)">查询</el-button>
        </div>
      </div>
    </el-card>

    <TablePage
      ref="tableRef"
      :columns="columns"
      :fetch="fetchReadings"
      :extra-query="{ r: range[0], g: range[1], gw: gatewayId, m: meterId, e: energy, f: onlyFail }"
      keyword-placeholder="按仪表编码 / 名称检索"
      :page-size="50"
      :max-height="480"
    >
      <template #col-reading="{ row }">
        <span :class="{ 'is-missing': row.reading === null }">{{ readingText(row.reading) }}</span>
      </template>
      <template #col-ok="{ row }">
        <el-tag :type="row.ok ? 'success' : 'danger'" size="small">{{ row.ok ? '成功' : '失败' }}</el-tag>
      </template>
      <template #col-raw="{ row }">
        <span class="t-mono raw">{{ row.ok ? row.raw || '—' : row.error || row.raw || '—' }}</span>
      </template>
      <template #toolbar>
        <el-button size="small" plain @click="exportCsv">
          <el-icon><Download /></el-icon>&nbsp;导出当前页
        </el-button>
      </template>
    </TablePage>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
.query__row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  &:not(:last-child) {
    margin-bottom: 12px;
  }
}
.query__label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  min-width: 56px;
}
.is-missing {
  color: var(--el-text-color-placeholder);
}
.raw {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
