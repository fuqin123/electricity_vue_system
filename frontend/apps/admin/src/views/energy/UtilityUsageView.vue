<script setup lang="ts">
/**
 * UtilityUsageView —— 水 / 汽计量（车间总表口径）
 *
 * 水与汽在页面上是同一件事：只有车间总表、不能拆到产线、按区间查用量。
 * 所以两页共用本组件（WaterView / SteamView 只是传不同 energy 的薄壳）——
 * 原来两份拷贝各自演化，才会出现「汽计量页明细表恒为空」只在其中一页被发现（B-519）。
 *
 * ⚠ 本页**没有线别筛选**：车间只有一块进水总表 / 一块蒸汽总表，
 *   任何「按产线的水/汽」都是折算出来的，不是计量出来的。要按线看只有电。
 */
import { computed } from 'vue';
import { energyModel, int, type PageQuery, type PageResult } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import ChartPanel from '@/components/ChartPanel.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { closedHourText, usageQuickSpans, usageQuickStart, useEnergyUsage, type UsageRow } from '@/utils/energyUsage';
import { notImplemented } from '@/utils/pending';

const props = defineProps<{ energy: 'water' | 'steam' }>();

const {
  meta,
  range,
  granularity,
  loading,
  data,
  load,
  rows,
  totals,
  missingBuckets,
  emptyHint,
  chartOption,
  extraQuery
} = useEnergyUsage(props.energy);

const isSteam = computed(() => props.energy === 'steam');
const QUICK = usageQuickSpans();

function applyQuick(hours: number, gran: 'hour' | 'day' | 'month') {
  // 终点取「最后一个已结算整点」：小时用量要等整点过后才算得出（v1.4.4）
  range.value = [usageQuickStart(hours), closedHourText()];
  granularity.value = gran;
  void load();
}

/** 区间合计（t） */
const totalT = computed(() => {
  const t = data.value?.totals;
  if (!t) return null;
  return props.energy === 'water' ? t.waterT : t.steamT;
});

/**
 * 列宽一律 min-width：全用固定 width 时 el-table 不会把剩余空间分给任何列，
 * 右侧就空出一大片（B-520 的「字段头分布不均」）。
 */
const columns: TableColumn<UsageRow>[] = [
  { prop: 'label', label: '时间', minWidth: 140, mono: true },
  { prop: 'usageText', label: `本时段用量（${meta.unit}）`, minWidth: 160, mono: true },
  { prop: 'deltaText', label: '较上一时段', minWidth: 130, mono: true },
  { prop: 'quality', label: '数据质量', minWidth: 130, slot: 'col-quality' }
];

function fetchHours(query: PageQuery): Promise<PageResult<UsageRow>> {
  const page = Math.max(1, query.page ?? 1);
  const size = Math.max(1, query.pageSize ?? 20);
  const start = (page - 1) * size;
  const kw = String(query.keyword ?? '').trim().toLowerCase();
  const filtered = kw ? rows.value.filter((r) => r.label.toLowerCase().includes(kw)) : rows.value;
  return Promise.resolve({
    list: filtered.slice(start, start + size),
    total: filtered.length,
    page,
    pageSize: size
  });
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" plain @click="notImplemented(`导出${isSteam ? '汽' : '水'}耗明细`)">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      :type="isSteam ? 'warning' : 'info'"
      :closable="false"
      show-icon
      class="mb-16"
      :title="isSteam ? '采集提醒' : '数据口径'"
      :description="
        isSteam
          ? '蒸汽表必须启用温压补偿，否则压力波动会被误算成用量波动。车间只有一块蒸汽总表，无法拆分到产线。'
          : '车间仅有一块进水总表，水耗无法拆分到产线，因此本页没有线别筛选。折算强度需用真实历史数据回填后才能用于考核。'
      "
    />

    <el-card shadow="never" class="mb-16">
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
        <span class="query__label">粒度</span>
        <el-radio-group v-model="granularity" size="small" @change="load">
          <el-radio-button value="hour">小时</el-radio-button>
          <el-radio-button value="day">日</el-radio-button>
          <el-radio-button value="month">月</el-radio-button>
        </el-radio-group>
        <el-button-group>
          <el-button v-for="q in QUICK" :key="q.label" size="small" @click="applyQuick(q.hours, q.gran)">
            {{ q.label }}
          </el-button>
        </el-button-group>
        <el-button type="primary" size="small" :loading="loading" @click="load">查询</el-button>
        <span class="query__hint">车间总表口径 · 不支持按产线筛选</span>
      </div>
    </el-card>

    <div class="stat-grid">
      <StatCard
        :label="`区间累计用${isSteam ? '汽' : '水'}`"
        :value="totalT === null || totalT === undefined ? '--' : totalT.toFixed(2)"
        unit="t"
        :color="meta.color"
        :loading="loading"
        :foot="totals ? `${totals.from} 至 ${totals.to} · ${totals.buckets} 个时段` : ''"
      />
      <StatCard
        :label="`区间折合用${isSteam ? '汽' : '水'}`"
        :value="totalT === null || totalT === undefined ? '--' : int(totalT)"
        unit="t"
        color="#86909c"
        :loading="loading"
        foot="只累加质量可信的小时差值"
      />
      <StatCard
        label="缺数时段"
        :value="data ? missingBuckets : '--'"
        unit="个"
        :color="missingBuckets > 0 ? '#bd7e00' : '#2a814b'"
        :loading="loading"
        foot="缺数显示 --，不补 0"
      />
      <StatCard
        :label="`模型基准${isSteam ? '汽耗' : '水耗'}强度`"
        :value="isSteam ? energyModel.steamPerBottleKg : energyModel.waterPerBottleL"
        :unit="isSteam ? 'kg/瓶' : 'L/瓶'"
        color="#1664ff"
        foot="实际强度需产量接入后才能算"
      />
    </div>

    <el-alert v-if="emptyHint" type="info" :closable="false" show-icon class="mb-16" :title="emptyHint" />

    <ChartPanel
      :title="`区间用${isSteam ? '汽' : '水'}趋势`"
      :option="chartOption"
      :height="280"
      note="整点差值 · 缺数处断开不连线"
      class="mt-16"
    />

    <div class="mt-16">
      <TablePage
        :columns="columns"
        :fetch="fetchHours"
        :extra-query="extraQuery"
        keyword-placeholder="按时间检索，如 09-24 08"
        :page-size="20"
        :max-height="420"
      >
        <template #col-quality="{ row }">
          <el-tag v-if="row.quality === 'ok'" type="success" size="small">完整</el-tag>
          <el-tag v-else-if="row.quality === 'partial'" type="warning" size="small">部分缺数</el-tag>
          <el-tag v-else-if="row.quality" type="info" size="small">缺数</el-tag>
          <span v-else>—</span>
        </template>
        <template #toolbar><span class="t-subtle">共 {{ rows.length }} 个时段（最新在上）</span></template>
      </TablePage>
    </div>
  </div>
</template>

<style scoped lang="scss">
.stat-grid { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
.mb-16 { margin-bottom: 16px; }
.query__row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.query__label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.query__hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}
</style>
