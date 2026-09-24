<script setup lang="ts">
/**
 * UsageQueryView —— 用量查询（按时间段）
 *
 * 这是「选一段时间、把这段时间的用量拉出来」的统一入口：
 *   · 时间：**精确到小时的起止区间**（年-月-日 时 → 年-月-日 时）
 *   · 粒度：小时 / 日 / 月（对应三张不同的数据来源，见后端 services/ranges.py）
 *   · 范围：车间合计 / 全部产线 / 单条产线
 *   · 曲线：**电 / 水 / 汽 自选**，勾哪几条画哪几条
 *
 * ⚠ 水与汽只有车间总表，**不存在线别的水/汽** —— 选「单条产线」时这两个勾选项
 *   直接禁用并写明原因。禁用比画一条全零线诚实：零会被读成「这条线没用过水」。
 *
 * 两个必须显式告诉使用者的前提（都写在界面上，不做静默处理）：
 *   ① **产量未接入**：产量列恒为空并标注「产量未接入」，否则「区间产量 0」会被读成
 *      「这段时间没生产」；
 *   ② **缺数显示 `--`，不是 0**：某小时只要有一块电表的差值算不出来，
 *      该小时的合计就是缺数。显示 0 会被当成「这一小时没用电」。
 */
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { energyApi, type UsageRangeResult } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import ChartPanel from '@/components/ChartPanel.vue';
import { energyUsageOption, type EnergyUsageSeries } from '@/utils/charts';
import { useSourceBanner } from '@/utils/source';

const banner = useSourceBanner('energy.range');

/* ── 查询条件 ─────────────────────────────────────────── */
type Granularity = 'hour' | 'day' | 'month';
type Scope = 'all' | 'workshop' | 'line';
type EnergyKey = 'electric' | 'water' | 'steam';

const GRAN_LABEL: Record<Granularity, string> = { hour: '小时', day: '天', month: '月' };
const ENERGY_COLOR: Record<EnergyKey, string> = { electric: '#1664ff', water: '#387bff', steam: '#bd7e00' };

const granularity = ref<Granularity>('hour');
const scope = ref<Scope>('all');
const lineCode = ref('A1');
/** 勾选要看的能源曲线。默认三条都看（车间口径下水汽本来就有数） */
const energies = ref<EnergyKey[]>(['electric', 'water', 'steam']);
/** 默认「今日 00:00 → 当前整点」—— 起止相同会只剩一个桶，别写成 hourAt(0) */
const range = ref<[string, string]>([dayStart(), hourNow()]);
const loading = ref(false);
const data = ref<UsageRangeResult & { fromHour?: string; toHour?: string } | null>(null);

/**
 * 本地时间字符串（`YYYY-MM-DD HH:mm`）。
 * ⚠ 不能用 `toISOString`：那是 UTC，东八区早上 8 点前会得到「昨天」（B-508 那条教训）。
 */
function fmt(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function hourNow(): string {
  return fmt(new Date());
}
function hourAt(hoursAgo: number): string {
  const d = new Date();
  d.setHours(d.getHours() - hoursAgo);
  return fmt(d);
}
function dayStart(): string {
  const d = new Date();
  return fmt(new Date(d.getFullYear(), d.getMonth(), d.getDate()));
}

const QUICK: { label: string; from: () => string; gran: Granularity }[] = [
  { label: '近 6 小时', from: () => hourAt(6), gran: 'hour' },
  { label: '今日', from: () => dayStart(), gran: 'hour' },
  { label: '近 3 日', from: () => hourAt(72), gran: 'hour' },
  { label: '近 30 日', from: () => hourAt(720), gran: 'day' },
  { label: '近 12 个月', from: () => hourAt(24 * 366), gran: 'month' }
];

function applyQuick(q: (typeof QUICK)[number]) {
  range.value = [q.from(), hourNow()];
  granularity.value = q.gran;
  void load();
}

/** 选了单条产线时，水/汽不可勾选（车间总表不拆线别） */
const perLine = computed(() => scope.value === 'line');

function onScopeChange() {
  if (perLine.value) energies.value = energies.value.filter((k) => k === 'electric');
  if (!energies.value.length) energies.value = ['electric'];
  void load();
}

async function load() {
  if (!range.value?.[0] || !range.value?.[1]) {
    ElMessage.warning('请选择起止时间');
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
/** 车间序列排在第一列，其余按产线编码顺序 */
const tableSeries = computed(() => {
  const list = data.value?.series ?? [];
  return [...list].sort((a, b) => (a.key === 'workshop' ? -1 : b.key === 'workshop' ? 1 : a.key.localeCompare(b.key)));
});

/** 表格行：每个时间桶一行；水/汽作为附加列（只在看它们时才有值） */
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
/** 电：车间合计，或所选单条产线 */
const electricData = computed(() => {
  const d = data.value;
  if (!d) return [];
  const wantKey = d.line ?? 'workshop';
  const s = d.series.find((x) => x.key === wantKey) ?? d.series[0];
  return s?.values ?? [];
});

const chartSeries = computed<EnergyUsageSeries[]>(() => {
  const d = data.value;
  if (!d) return [];
  const all: Record<EnergyKey, EnergyUsageSeries> = {
    electric: {
      key: 'electric',
      name: d.line ? `${d.line} 线用电` : '车间用电',
      unit: 'kWh',
      color: ENERGY_COLOR.electric,
      data: electricData.value
    },
    water: {
      key: 'water',
      name: '车间用水',
      unit: 't',
      color: ENERGY_COLOR.water,
      data: d.utility.water,
      emptyHint: '车间进水总表未接入'
    },
    steam: {
      key: 'steam',
      name: '车间用汽',
      unit: 't',
      color: ENERGY_COLOR.steam,
      data: d.utility.steam,
      emptyHint: '蒸汽总表本区间无数据'
    }
  };
  return energies.value.map((k) => all[k]);
});

const chartOption = computed(() => {
  const d = data.value;
  if (!d || !chartSeries.value.length) return null;
  return energyUsageOption(d.labels, chartSeries.value, {
    labelInterval: d.labels.length > 24 ? 2 : 'auto'
  });
});

/** 勾了但本区间没数据的能源 —— 明确说出来，别让人以为图漏画了 */
const emptyEnergies = computed(() =>
  chartSeries.value.filter((s) => !s.data.some((v) => v !== null && v !== undefined)).map((s) => s.emptyHint ?? `${s.name}本区间无数据`)
);

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
  const csv = '﻿' + lines.join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  const stamp = (d.fromHour ?? d.from).replace(/[^0-9]/g, '').slice(0, 10);
  const stamp2 = (d.toHour ?? d.to).replace(/[^0-9]/g, '').slice(0, 10);
  a.download = `用量查询_${stamp}_${stamp2}_${d.granularity}.csv`;
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
            <el-button v-for="q in QUICK" :key="q.label" size="small" @click="applyQuick(q)">
              {{ q.label }}
            </el-button>
          </el-button-group>
        </div>

        <div class="query__row">
          <span class="query__label">统计粒度</span>
          <el-radio-group v-model="granularity" size="small" @change="load">
            <el-radio-button value="hour">小时</el-radio-button>
            <el-radio-button value="day">日</el-radio-button>
            <el-radio-button value="month">月</el-radio-button>
          </el-radio-group>

          <span class="query__label">统计范围</span>
          <el-select v-model="scope" size="small" style="width: 160px" @change="onScopeChange">
            <el-option label="车间合计 + 各线" value="all" />
            <el-option label="仅车间合计" value="workshop" />
            <el-option label="单条产线" value="line" />
          </el-select>
          <el-select v-if="scope === 'line'" v-model="lineCode" size="small" style="width: 110px" @change="load">
            <el-option v-for="c in ['A1', 'A2', 'A3', 'A4', 'A5', 'A6']" :key="c" :label="c" :value="c" />
          </el-select>

          <span class="query__label">能源曲线</span>
          <el-checkbox-group v-model="energies" size="small" @change="load">
            <el-checkbox-button value="electric">电</el-checkbox-button>
            <el-checkbox-button value="water" :disabled="perLine">水</el-checkbox-button>
            <el-checkbox-button value="steam" :disabled="perLine">汽</el-checkbox-button>
          </el-checkbox-group>
          <el-tooltip v-if="perLine" content="水与汽只有车间总表，无法拆分到产线" placement="top">
            <span class="query__hint">水/汽不可按线查看</span>
          </el-tooltip>
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
        :foot="data ? `${data.fromHour ?? data.from} 至 ${data.toHour ?? data.to} · ${data.bucketCount} 个${GRAN_LABEL[data.granularity]}` : ''"
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

    <el-alert
      v-if="emptyEnergies.length"
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      :title="`已勾选但图上没有的曲线：${emptyEnergies.join('；')}`"
    />

    <!-- 趋势 -->
    <ChartPanel
      v-if="chartOption"
      title="区间能源趋势图"
      :option="chartOption"
      :height="280"
      note="每种能源一根轴（单位不同不可共轴）· 缺数处断开不连线"
      class="mb-16"
    />

    <!-- 明细 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>用量明细</span>
          <span class="card-head__note">
            电量单位 kWh · 水汽单位 t · 缺数显示 <code>--</code> · 产量列已留空（接口预留）
          </span>
        </div>
      </template>

      <!--
        v-if 只是给「本区间无可显示时段」一个明确空态。
        ⚠ 这里曾经排查过一次「动态列全部不渲染」，真因不在 el-table：
        是上方 ChartPanel 的 mounted 里 setOption 抛了 `yAxis "0" not found`
        （图表工厂把 yAxisIndex 算错了），**mounted 钩子抛异常会中断整棵子树挂载**，
        于是同页后面的表格一列都注册不上。症状在下游，病因在上游 —— 见 docs/02 B-521。
      -->
      <el-table
        v-if="seriesKeys.length"
        v-loading="loading"
        :data="rows"
        size="small"
        height="380"
        border
      >
        <el-table-column prop="label" label="时间" min-width="120" />
        <el-table-column
          v-for="(key, idx) in seriesKeys"
          :key="key"
          :label="tableSeries[idx].name"
          min-width="120"
          align="right"
        >
          <template #default="{ row }">
            <span :class="{ 'is-missing': row.values[idx] === null }">{{ fmtCell(row.values[idx], 1) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="用水(t)" min-width="110" align="right">
          <template #default="{ row }">
            <span :class="{ 'is-missing': row.water === null }">{{ fmtCell(row.water, 2) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="用汽(t)" min-width="110" align="right">
          <template #default="{ row }">
            <span :class="{ 'is-missing': row.steam === null }">{{ fmtCell(row.steam, 2) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="数据质量" min-width="110">
          <template #default="{ row }">
            <el-tag v-if="row.quality === 'ok'" type="success" size="small">完整</el-tag>
            <el-tag v-else-if="row.quality === 'partial'" type="warning" size="small">部分缺数</el-tag>
            <el-tag v-else-if="row.quality" type="info" size="small">缺数</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="本区间没有可显示的时段，换一段时间或放宽筛选再查" :image-size="72" />
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
.query__hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
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
