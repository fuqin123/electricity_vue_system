<script setup lang="ts">
/**
 * DashboardView —— 数据中心（后台首页）
 * 指标卡 + 三类趋势图 + 最新告警。数据来自 mock，接真实接口时无需改本页。
 */
import { computed, onMounted, ref } from 'vue';
import { adminApi, big, energyModel, int, type AdminOverview } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import ChartPanel from '@/components/ChartPanel.vue';
import AlarmLevelTag from '@/components/AlarmLevelTag.vue';
import { outputKwhOption, unitKwhCompareOption } from '@/utils/charts';
import { notImplemented } from '@/utils/pending';

const loading = ref(true);
const data = ref<AdminOverview | null>(null);
const range = ref<'day' | 'month' | 'year'>('day');

async function load() {
  loading.value = true;
  try {
    data.value = await adminApi.getOverview();
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const kpi = computed(() => data.value?.kpi);

/** 单位电耗达成情况（越低越好） */
const unitTrend = computed<'up' | 'down' | 'flat'>(() => {
  const d = data.value;
  if (!d) return 'flat';
  const diff = d.kpi.unitKwh - energyModel.targetUnitKwh;
  if (Math.abs(diff) < 0.05) return 'flat';
  return diff > 0 ? 'up' : 'down';
});

const trendOption = computed(() => {
  const d = data.value;
  if (!d) return null;
  if (range.value === 'month') return outputKwhOption(d.daily30, { labelInterval: 2 });
  if (range.value === 'year')
    return outputKwhOption(d.monthly12, { outDiv: 10000, kwhDiv: 10000 });
  return outputKwhOption(d.hourly, { outDiv: 1000, kwhDiv: 1000, outUnit: '千瓶', kwhUnit: '千kWh', labelInterval: 1 });
});

const compareOption = computed(() =>
  data.value ? unitKwhCompareOption(data.value.lineUnitKwh, energyModel.targetUnitKwh) : null
);

const monthlyOption = computed(() => (data.value ? outputKwhOption(data.value.monthly12, { labelInterval: 0 }) : null));

function reloadAll() {
  void load();
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button :loading="loading" @click="reloadAll">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
        <el-button type="primary" plain @click="notImplemented('导出看板快照')">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
      </template>
    </PageHeader>

    <!-- 指标卡 -->
    <div class="stat-grid">
      <StatCard
        label="今日产量"
        :value="big(kpi?.todayOutput ?? 0, '').v"
        :unit="big(kpi?.todayOutput ?? 0, '瓶').u"
        color="#1664ff"
        :loading="loading"
        :foot="`昨日同期 ${big(data?.kpi.todayOutput ? data.kpi.todayOutput * 0.96 : 0, '').v} 万瓶`"
      />
      <StatCard
        label="今日用电"
        :value="big(kpi?.todayKwh ?? 0, '').v"
        :unit="big(kpi?.todayKwh ?? 0, 'kWh').u"
        color="#387bff"
        :loading="loading"
        :foot="`折合 ${int(kpi?.todayKwh ?? 0)} kWh`"
      />
      <StatCard
        label="综合单位电耗"
        :value="(kpi?.unitKwh ?? 0).toFixed(2)"
        unit="kWh/千瓶"
        :color="unitTrend === 'up' ? '#d7312a' : '#2a814b'"
        :trend="unitTrend"
        :loading="loading"
        :foot="`考核目标 ${energyModel.targetUnitKwh.toFixed(2)} · ${
          (kpi?.unitKwh ?? 0) > energyModel.targetUnitKwh ? '未达标' : '达标'
        }`"
      />
      <StatCard
        label="未处理告警"
        :value="kpi?.activeAlarms ?? 0"
        unit="条"
        color="#d7312a"
        :loading="loading"
        foot="含严重与重要告警，需人工确认"
      />
      <StatCard
        label="采集网关在线"
        :value="`${kpi?.onlineGateways ?? 0} / ${kpi?.totalGateways ?? 0}`"
        color="#2a814b"
        :loading="loading"
        :foot="`离线 ${(kpi?.totalGateways ?? 0) - (kpi?.onlineGateways ?? 0)} 台，需排查链路`"
      />
      <StatCard
        label="数据上报完整率"
        :value="(kpi?.dataQualityRate ?? 0).toFixed(1)"
        unit="%"
        color="#bd7e00"
        :loading="loading"
        foot="各产线整点应报点位的实收比例"
      />
    </div>

    <!-- 趋势图 -->
    <div class="card">
      <div class="card__head">
        <h3 class="card__title">产量与用电趋势</h3>
        <el-radio-group v-model="range" size="small">
          <el-radio-button value="day">今日分时</el-radio-button>
          <el-radio-button value="month">近 30 日</el-radio-button>
          <el-radio-button value="year">近 12 个月</el-radio-button>
        </el-radio-group>
      </div>
      <el-skeleton v-if="loading" :rows="6" animated />
      <ChartPanel v-else title="" :option="trendOption" :height="300" class="chart-inner" />
    </div>

    <!-- 对标 + 月度 -->
    <div class="grid-2">
      <div class="card">
        <div class="card__head">
          <h3 class="card__title">各产线单位电耗对标</h3>
          <span class="card__extra">目标 {{ energyModel.targetUnitKwh.toFixed(2) }} kWh/千瓶</span>
        </div>
        <ChartPanel v-if="!loading" title="" :option="compareOption" :height="260" class="chart-inner" />
        <el-skeleton v-else :rows="5" animated />
      </div>

      <div class="card">
        <div class="card__head">
          <h3 class="card__title">近 12 个月产量与用电</h3>
          <span class="card__extra">当月为进行中</span>
        </div>
        <ChartPanel v-if="!loading" title="" :option="monthlyOption" :height="260" class="chart-inner" />
        <el-skeleton v-else :rows="5" animated />
      </div>
    </div>

    <!-- 最新告警 -->
    <div class="card">
      <div class="card__head">
        <h3 class="card__title">最新告警</h3>
        <el-button link type="primary" @click="$router.push('/alarm/realtime')">
          查看全部<el-icon><ArrowRight /></el-icon>
        </el-button>
      </div>
      <el-table v-loading="loading" :data="data?.latestAlarms ?? []" size="default">
        <el-table-column prop="code" label="告警编号" width="180">
          <template #default="{ row }"><span class="t-mono">{{ row.code }}</span></template>
        </el-table-column>
        <el-table-column label="级别" width="80">
          <template #default="{ row }"><AlarmLevelTag :level="row.level" /></template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="160" show-overflow-tooltip />
        <el-table-column prop="content" label="内容" min-width="240" show-overflow-tooltip />
        <el-table-column label="触发值 / 阈值" width="140">
          <template #default="{ row }"><span class="t-mono">{{ row.value }} / {{ row.threshold }}</span></template>
        </el-table-column>
        <el-table-column prop="occurredAt" label="发生时间" width="180">
          <template #default="{ row }"><span class="t-mono">{{ row.occurredAt }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'danger' : row.status === 'acked' ? 'warning' : 'success'" size="small">
              {{ row.status === 'active' ? '未处理' : row.status === 'acked' ? '已确认' : '已恢复' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped lang="scss">
.stat-grid { margin-bottom: 16px; }

.grid-2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.card + .card { margin-top: 16px; }

/* 内嵌图表卡：去掉标题栏留白，避免卡片套卡片 */
.chart-inner {
  border: none;
  padding: 0;

  :deep(.card__head) { display: none; }
}
</style>
