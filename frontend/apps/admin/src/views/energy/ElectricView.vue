<script setup lang="ts">
/**
 * ElectricView —— 电计量
 * 产线配电柜智能电表的区间电量与单位电耗。
 * ⚠ 电表为累计值（总有功电能），入库时做差分得到小时用电量；
 *   换表 / 归零 / 通讯中断会在差分后产生尖峰，采集侧必须加异常值过滤。
 *
 * v1.4.0：加**起止时间（精确到小时）**，取数改走 /api/energy/range
 * （与用量查询页、大屏同源）。明细表此前恒为空，见 B-519。
 * v1.5.2：筛选从「线别」改为 **车间 → 用能类型 → 设备** 三级联动 ——
 *   辅机 / 瓶胚 / 调配电表在接口表里没有「线别」，用旧维度根本选不出来。
 */
import { big, int, thresholds, type PageQuery, type PageResult } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import ChartPanel from '@/components/ChartPanel.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { closedHourText, usageQuickSpans, usageQuickStart, useEnergyUsage, type UsageRow } from '@/utils/energyUsage';
import { notImplemented } from '@/utils/pending';

const {
  range,
  granularity,
  loading,
  data,
  load,
  rows,
  totals,
  missingBuckets,
  coverageHint,
  chartOption,
  extraQuery,
  tree,
  treeLoading,
  workshop,
  usageType,
  node,
  typeOptions,
  nodeOptions,
  focused,
  selectionLabel
} = useEnergyUsage('electric');

const QUICK = usageQuickSpans();

function applyQuick(hours: number, gran: 'hour' | 'day' | 'month') {
  // 终点取「最后一个已结算整点」：小时用量要等整点过后才算得出（v1.4.4）
  range.value = [usageQuickStart(hours), closedHourText()];
  granularity.value = gran;
  void load();
}

/**
 * 列宽一律用 min-width 而不是 width：
 * 全固定宽度时 el-table 不会把剩余空间分给任何列，右侧就空出一大片（B-520）。
 */
const columns: TableColumn<UsageRow>[] = [
  { prop: 'label', label: '时间', minWidth: 130, mono: true },
  { prop: 'usageText', label: '用电量（kWh）', minWidth: 140, mono: true },
  { prop: 'deltaText', label: '较上一时段', minWidth: 120, mono: true },
  { prop: 'quality', label: '数据质量', minWidth: 120, slot: 'col-quality' }
];

function fetchHours(query: PageQuery): Promise<PageResult<UsageRow>> {
  const page = Math.max(1, query.page ?? 1);
  const size = Math.max(1, query.pageSize ?? 20);
  const start = (page - 1) * size;
  // 明细来自区间结果，行数就是桶数；缺数行保留（显示 --），不偷偷剔掉
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
        <el-button @click="notImplemented('手动补采')"><el-icon><Refresh /></el-icon>&nbsp;手动补采</el-button>
        <el-button type="primary" plain @click="notImplemented('导出电量明细')">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
      </template>
    </PageHeader>

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
        <!-- 三级联动：车间 → 用能类型 → 设备/节点（v1.5.2） -->
        <span class="query__label">车间</span>
        <el-select
          v-model="workshop"
          size="small"
          style="width: 140px"
          :loading="treeLoading"
          placeholder="选择车间"
          @change="load"
        >
          <el-option
            v-for="w in tree"
            :key="w.workshopCode"
            :label="w.workshop"
            :value="w.workshopCode"
          />
        </el-select>
        <span class="query__label">类型</span>
        <el-select
          v-model="usageType"
          size="small"
          style="width: 150px"
          clearable
          placeholder="全部类型"
          @change="load"
        >
          <el-option
            v-for="t in typeOptions"
            :key="t.usageType"
            :label="`${t.usageType}（${t.devices.length}）`"
            :value="t.usageType"
          />
        </el-select>
        <span class="query__label">设备</span>
        <el-select
          v-model="node"
          size="small"
          style="width: 220px"
          clearable
          :disabled="!usageType"
          :placeholder="usageType ? '全部设备' : '请先选类型'"
          @change="load"
        >
          <el-option
            v-for="n in nodeOptions"
            :key="n.code"
            :label="n.count > 1 ? `${n.name} · 整节点（${n.count} 块表）` : `${n.name} · 单表`"
            :value="n.code"
          />
        </el-select>
        <el-button-group>
          <el-button
            v-for="q in QUICK"
            :key="q.label"
            size="small"
            @click="applyQuick(q.hours, q.gran)"
          >
            {{ q.label }}
          </el-button>
        </el-button-group>
        <el-button type="primary" size="small" :loading="loading" @click="load">查询</el-button>
      </div>
      <!-- 说明当前查询范围：三级下拉选完就收起来了，不写出来没人知道看的是哪几个点位 -->
      <div class="query__hint">
        <el-tag size="small" type="info" effect="plain">{{ selectionLabel }}</el-tag>
        <span v-if="focused" class="query__hint-text">已按所选设备单独出数</span>
        <span v-else class="query__hint-text">车间合计（本车间全部计量点）</span>
        <span v-if="data?.meterCount !== undefined" class="query__hint-text">
          · 命中 {{ data.meterCount }} 个计量点
        </span>
      </div>
    </el-card>

    <el-alert
      v-if="coverageHint"
      :type="data?.coverage?.missing?.length ? 'warning' : 'info'"
      :closable="false"
      show-icon
      class="mb-16"
      title="数据覆盖"
      :description="coverageHint"
    />

    <div class="stat-grid">
      <StatCard
        label="区间用电量"
        :value="data ? big(data.totals.kwh ?? 0, '').v : '--'"
        :unit="data ? big(data.totals.kwh ?? 0, 'kWh').u : 'kWh'"
        color="#1664ff"
        :loading="loading"
        :foot="totals ? `${totals.from} 至 ${totals.to} · ${totals.buckets} 个时段` : ''"
      />
      <StatCard
        label="区间折合电量"
        :value="data ? int(data.totals.kwh ?? 0) : '--'"
        unit="kWh"
        color="#387bff"
        :loading="loading"
        foot="只累加质量可信的小时差值"
      />
      <StatCard
        label="缺数时段"
        :value="data ? missingBuckets : '--'"
        unit="个"
        :color="missingBuckets > 0 ? '#bd7e00' : '#2a814b'"
        :loading="loading"
        foot="缺数显示 --，不补 0（补 0 会被读成「这一小时没用电」）"
      />
      <StatCard
        label="单位电耗告警线"
        :value="thresholds.unitKwhHigh.toFixed(2)"
        unit="kWh/千瓶"
        color="#d7312a"
        :foot="'按线标准见「标准单耗」页；产量接入后才能算实际值'"
      />
    </div>

    <ChartPanel
      :title="`区间用电趋势 · ${selectionLabel}`"
      :option="chartOption"
      :height="280"
      note="缺数处断开不连线；产量未接入，故不叠单位电耗曲线"
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
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.query__hint-text {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
