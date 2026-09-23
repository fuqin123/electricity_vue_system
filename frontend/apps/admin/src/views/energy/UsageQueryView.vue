<script setup lang="ts">
/**
 * UsageQueryView —— 用量查询（按时间段）
 *
 * 这是「选一个时间段、把这段时间的用量拉出来」的统一入口：
 *   · 时间：快捷区间（今日 / 近 7 日 / 近 30 日 / 本月）+ 自定义日期区间
 *   · 粒度：小时 / 日 / 月（对应三张不同的数据来源，见后端 services/ranges.py）
 *   · 范围：车间合计 / 全部产线（车间 + 各线一列一列排开）/ 单条产线
 *
 * 两个必须显式告诉使用者的事（都写在界面上，不做静默处理）：
 *   ① **产量未接入**：产量列恒为 0，界面标注「产量未接入」，
 *      否则「区间产量 0」会被读成「这段时间没生产」。
 *   ② **缺数显示 `--`，不是 0**：某小时里只要有一块电表的差值算不出来，
 *      该小时的合计就是缺数。数据显示为 0 会被当成「这一小时没用电」。
 */
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { energyApi, type TrendPoint, type UsageRangeResult } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import ChartPanel from '@/components/ChartPanel.vue';
import { outputKwhOption } from '@/utils/charts';
import { useSourceBanner } from '@/utils/source';

const banner = useSourceBanner('energy.range');

/* ── 查询条件 ─────────────────────────────────────────── */
type Granularity = 'hour' | 'day' | 'month';
type Scope = 'all' | 'workshop' | 'line';

const granularity = ref<Granularity>('day');
const scope = ref<Scope>('all');
const lineCode = ref('A1');
const range = ref<[string, string]>([isoOffset(-6), isoToday()]);
const loading = ref(false);
const data = ref<UsageRangeResult | null>(null);

/** 本地日期字符串（不能用 toISOString：那是 UTC，东八区会差一天） */
function fmt(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function isoToday(): string {
  return fmt(new Date());
}
function isoOffset(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return fmt(d);
}

const QUICK: { label: string; from: () => string; to: () => string; gran: Granularity }[] = [
  { label: '今日', from: () => isoToday(), to: () => isoToday(), gran: 'hour' },
  { label: '近 7 日', from: () => isoOffset(-6), to: () => isoToday(), gran: 'day' },
  { label: '近 30 日', from: () => isoOffset(-29), to: () => isoToday(), gran: 'day' },
  {
    label: '本月',
    from: () => fmt(new Date(new Date().getFullYear(), new Date().getMonth(), 1)),
    to: () => isoToday(),
    gran: 'day'
  }
];

function applyQuick(q: (typeof QUICK)[number]) {
  range.value = [q.from(), q.to()];
  granularity.value = q.gran;
  void load();
}

async function load() {
  if (!range.value?.[0] || !range.value?.[1]) {
    ElMessage.warning('请选择时间范围');
    return;
  }
  loading.value = true;
  try {
    data.value = await energyApi.getRange({
      from: range.value[0],
      to: range.value[1],
      granularity: granularity.value,
      scope: scope.value,
      line: scope.value === 'line' ? lineCode.value : undefined
    });
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    loading.value = false;
  }
}

onMounted(load);

/* ── 表格 ─────────────────────────────────────────────── */
/** 车间序列排在第一列，其余按产线顺序 */
const tableSeries = computed(() => {
  const list = data.value?.series ?? [];
  return [...list].sort((a, b) => (a.key === 'workshop' ? -1 : b.key === 'workshop' ? 1 : a.key.localeCompare(b.key)));
});

/** 表格行：每个时间桶一行 */
const rows = computed(() => {
  const d = data.value;
  if (!d) return [];
  return d.labels.map((label, i) => ({
    label,
    quality: d.quality?.[i] ?? '',
    values: tableSeries.value.map((s) => s.values[i] ?? null),
    water: d.utility.water[i] ?? null,
    steam: d.utility.steam[i] ?? null
  }));
});

const seriesKeys = computed(() => tableSeries.value.map((s) => s.key));

/** 缺数的行数：用来在界面上说明「这批数据不完整」，而不是让人自己一行行看 */
const missingRows = computed(() => rows.value.filter((r) => r.values[0] === null).length);

function fmtCell(v: number | null, digits = 1): string {
  if (v === null || v === undefined) return '--';
  return v.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

/* ── 图表 ─────────────────────────────────────────────── */
const chartOption = computed(() => {
  const d = data.value;
  if (!d || !d.series.length) return null;
  const wsKey = d.series.find((s) => s.key === 'workshop') ?? d.series[0];
  const points: TrendPoint[] = d.labels.map((label, i) => ({
    label,
    // 产量未接入 → null（图上不画柱、tooltip 显示 --）。写 0 会被读成「这段时间没生产」（ADR-013）。
    output: null,
    // 缺数一律留 null：补 0 会让趋势图在断采的地方画出一条「归零又弹回」的假曲线
    kwh: wsKey.values[i] ?? null
  }));
  return outputKwhOption(points, { labelInterval: d.labels.length > 24 ? 2 : 'auto' });
});

/* ── 导出 CSV ─────────────────────────────────────────── */
function exportCsv() {
  const d = data.value;
  if (!d || !rows.value.length) {
    ElMessage.warning('当前没有可导出的数据');
    return;
  }
  const head = ['时间', ...tableSeries.value.map((s) => `${s.name}(kWh)`), '用水(t)', '用汽(t)', '数据质量'];
  const lines = [head.join(',')];
  rows.value.forEach((r) => {
    lines.push(
      [
        r.label,
        ...r.values.map((v) => (v === null ? '' : String(v))),
        r.water === null ? '' : String(r.water),
        r.steam === null ? '' : String(r.steam),
        r.quality
      ].join(',')
    );
  });
  // ⚠ 必须加 UTF-8 BOM，否则 Excel 打开中文表头会乱码
  const csv = '\ufeff' + lines.join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `用量查询_${d.from}_${d.to}_${d.granularity}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  ElMessage.success('已导出 CSV（缺数格为空，不是 0）');
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button :loading="loading" @click="load"><el-icon><Refresh /></el-icon>&nbsp;查询</el-button>
        <el-button type="primary" plain @click="exportCsv">
          <el-icon><Download /></el-icon>&nbsp;导出 CSV
        </el-button>
      </template>
    </PageHeader>

    <el-alert v-if="banner.show" type="warning" :closable="false" show-icon class="mb-16" :title="banner.text" />

    <!-- 查询条件 -->
    <el-card shadow="never" class="mb-16">
      <div class="query">
        <div class="query__row">
          <span class="query__label">快捷区间</span>
          <el-button-group>
            <el-button v-for="q in QUICK" :key="q.label" size="small" @click="applyQuick(q)">
              {{ q.label }}
            </el-button>
          </el-button-group>

          <span class="query__label">自定义</span>
          <el-date-picker
            v-model="range"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            :clearable="false"
            style="width: 250px"
          />
        </div>

        <div class="query__row">
          <span class="query__label">统计粒度</span>
          <el-radio-group v-model="granularity" size="small" @change="load">
            <el-radio-button value="hour">小时</el-radio-button>
            <el-radio-button value="day">日</el-radio-button>
            <el-radio-button value="month">月</el-radio-button>
          </el-radio-group>

          <span class="query__label">统计范围</span>
          <el-select v-model="scope" size="small" style="width: 160px" @change="load">
            <el-option label="车间合计 + 各线" value="all" />
            <el-option label="仅车间合计" value="workshop" />
            <el-option label="单条产线" value="line" />
          </el-select>
          <el-select v-if="scope === 'line'" v-model="lineCode" size="small" style="width: 110px" @change="load">
            <el-option v-for="c in ['A1', 'A2', 'A3', 'A4', 'A5', 'A6']" :key="c" :label="c" :value="c" />
          </el-select>
        </div>
      </div>
    </el-card>

    <!-- 汇总 -->
    <div class="stat-grid mb-16">
      <StatCard
        label="区间用电合计"
        :value="data ? fmtCell(data.totals.kwh, 1) : '--'"
        unit="kWh"
        color="#1664ff"
        :loading="loading"
        :foot="data ? `${data.from} 至 ${data.to} · ${data.bucketCount} 个${granularity === 'hour' ? '小时' : granularity === 'day' ? '天' : '月'}` : ''"
      />
      <StatCard
        label="区间产量合计"
        :value="data?.productionAvailable ? fmtCell(data.totals.output, 0) : '--'"
        unit="瓶"
        color="#86909c"
        :loading="loading"
        :foot="data?.productionAvailable ? '' : '产量数据源未接入（预留接口）'"
      />
      <StatCard
        label="区间用水合计"
        :value="data ? fmtCell(data.totals.waterT, 2) : '--'"
        unit="t"
        color="#387bff"
        :loading="loading"
        foot="车间总表"
      />
      <StatCard
        label="区间用汽合计"
        :value="data ? fmtCell(data.totals.steamT, 2) : '--'"
        unit="t"
        color="#ec48bd"
        :loading="loading"
        foot="车间总表（真实采集）"
      />
    </div>

    <el-alert
      v-if="missingRows"
      type="warning"
      :closable="false"
      show-icon
      class="mb-16"
      :title="`共 ${data?.bucketCount} 个时段，其中 ${missingRows} 个时段缺数（显示为 --）`"
      description="缺数表示该时段内并非所有电表都能算出可信差值（采集未覆盖或累计值回退）。缺数不补 0 —— 补 0 会被误读成「这段时间没用电」。"
    />

    <!-- 趋势 -->
    <ChartPanel
      v-if="chartOption"
      title="区间用电量趋势（车间合计）"
      :option="chartOption"
      :height="240"
      note="柱＝用电量；产量未接入，曲线暂不显示"
      class="mb-16"
    />

    <!-- 明细 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>用量明细</span>
          <span class="card-head__note">
            单位 kWh · 缺数显示 <code>--</code> · 产量列已留空（接口预留）
          </span>
        </div>
      </template>

      <el-table v-loading="loading" :data="rows" size="small" height="380" border>
        <el-table-column prop="label" label="时间" width="110" fixed />
        <el-table-column
          v-for="(key, idx) in seriesKeys"
          :key="key"
          :label="tableSeries[idx].name"
          min-width="110"
          align="right"
        >
          <template #default="{ row }">
            <span :class="{ 'is-missing': row.values[idx] === null }">{{ fmtCell(row.values[idx], 1) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="用水(t)" width="96" align="right">
          <template #default="{ row }">
            <span :class="{ 'is-missing': row.water === null }">{{ fmtCell(row.water, 2) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="用汽(t)" width="96" align="right">
          <template #default="{ row }">
            <span :class="{ 'is-missing': row.steam === null }">{{ fmtCell(row.steam, 2) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="数据质量" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.quality === 'ok'" type="success" size="small">完整</el-tag>
            <el-tag v-else-if="row.quality === 'partial'" type="warning" size="small">部分缺数</el-tag>
            <el-tag v-else-if="row.quality" type="info" size="small">缺数</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped lang="scss">
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
  min-width: 62px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-head__note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.is-missing {
  color: var(--el-text-color-placeholder);
}
</style>
