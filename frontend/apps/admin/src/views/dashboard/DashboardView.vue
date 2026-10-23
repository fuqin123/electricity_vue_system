<script setup lang="ts">
/**
 * DashboardView —— 数据中心（后台首页）
 *
 * v1.4.0 改版要点（口径全部来自 /api/energy/dashboard，与用量查询页、大屏同源）：
 *  ① 顶部是**起止时间（精确到小时）**，整页数字都按这个区间出；
 *  ② 主趋势图 = **单箱变动费用**，叠加「每月标准变动费用」水平虚线做对比；
 *  ③ 三张**按能源分**的累计趋势图（电为主图、水/汽为辅），每张含累计用量 / 累计产量 / 累计单耗；
 *  ④ 原「今日用量统计」删除，改为**区间累计用量**显示。
 *
 * v1.4.4 改版要点（现场反馈的版式问题）：
 *  ① 顶部 KPI 去掉「采集网关在线」一行卡片 —— 1920 宽下 7 张正好排满一行，
 *     第 8 张会单独换行；网关在线与否属于设备台账信息，去「设备与采集 → 采集网关」看；
 *  ② 修「下方两列底边不齐」：并排卡片误吃全局 `.card + .card` 的纵向外边距
 *     （见样式区 `.grid-2 > .card + .card`）。
 *
 * ⚠ 产量与部分单价尚未接入，所以「累计产量 / 累计单耗 / 单箱变动费用」现在**必然为空**。
 *   这是如实留空，不是坏了：界面必须把 cost.blockers 的原因显示出来，
 *   也绝不拿电量反推产量凑一个数（ADR-013 / B-406 同一条铁律）。
 */
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import {
  adminApi,
  big,
  energyApi,
  int,
  productionDayStart,
  type AdminOverview,
  type DashboardResult,
  type DashboardEnergyBlock
} from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import ChartPanel from '@/components/ChartPanel.vue';
import AlarmLevelTag from '@/components/AlarmLevelTag.vue';
import { costPerCaseOption, energyCumulativeOption, unitKwhCompareOption } from '@/utils/charts';
import { notImplemented } from '@/utils/pending';

/* ── 时间区间（精确到小时）─────────────────────────────── */

function fmt(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:00`;
}

function defaultRange(): [string, string] {
  const now = new Date();
  // 生产日 06:00 → 现在；06:00 前 productionDayStart 会退到前一天 06:00
  return [fmt(productionDayStart(now)), fmt(now)];
}

const range = ref<[string, string]>(defaultRange());
const granularity = ref<'hour' | 'day'>('hour');
const loading = ref(true);
const dash = ref<DashboardResult | null>(null);
const overview = ref<AdminOverview | null>(null);

const QUICK: { label: string; span: number; gran: 'hour' | 'day' }[] = [
  { label: '本小时起', span: 0, gran: 'hour' },
  { label: '本生产日', span: 0, gran: 'hour' },
  { label: '近 3 日', span: 3, gran: 'hour' },
  { label: '近 30 日', span: 30, gran: 'day' }
];

function applyQuick(q: (typeof QUICK)[number]) {
  const now = new Date();
  if (q.label === '本小时起') {
    range.value = [fmt(now), fmt(now)];
  } else if (q.span === 0) {
    range.value = [fmt(productionDayStart(now)), fmt(now)];
  } else {
    const from = new Date(now.getTime() - q.span * 86400000);
    range.value = [fmt(from), fmt(now)];
  }
  granularity.value = q.gran;
  void load();
}

async function load() {
  if (!range.value?.[0] || !range.value?.[1]) {
    ElMessage.warning('请选择起止时间');
    return;
  }
  loading.value = true;
  try {
    // 两个请求互不依赖，并发出去；看板卡片与图表因此永远同一时刻出数
    const [d, o] = await Promise.all([
      energyApi.getDashboard({ from: range.value[0], to: range.value[1], granularity: granularity.value }),
      adminApi.getOverview()
    ]);
    dash.value = d;
    overview.value = o;
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    loading.value = false;
  }
}

onMounted(load);

/* ── 区间累计 KPI ─────────────────────────────────────── */

const t = computed(() => dash.value?.totals ?? null);
const kpi = computed(() => overview.value?.kpi);

/** 单箱变动费用：算不出就是 null（显示 --），不能显示 0 */
const perCaseText = computed(() => {
  const v = t.value?.perCaseCost;
  return v === null || v === undefined ? '--' : v.toFixed(2);
});

/* ── 图表 ─────────────────────────────────────────────── */

const labelInterval = computed(() => {
  const n = dash.value?.bucketCount ?? 0;
  if (n <= 12) return 0;
  if (n <= 31) return 2;
  return Math.floor(n / 10);
});

const costOption = computed(() => {
  const d = dash.value;
  if (!d) return null;
  return costPerCaseOption(d.labels, d.cost.perCase, d.cost.standardPerCase, {
    labelInterval: labelInterval.value
  });
});

function blockOf(key: 'electric' | 'water' | 'steam'): DashboardEnergyBlock | null {
  return dash.value?.energies[key] ?? null;
}

const unitUseLabel = (usageUnit: string) => `${usageUnit}/千瓶`;

const electricOption = computed(() => {
  const d = dash.value;
  const b = blockOf('electric');
  if (!d || !b) return null;
  return energyCumulativeOption({
    labels: d.labels,
    usage: b.cumulative,
    usageUnit: b.unit,
    output: b.outputCumulative,
    unitUse: b.unitCumulative,
    unitUseLabel: unitUseLabel(b.unit),
    color: '#1664ff',
    labelInterval: labelInterval.value
  });
});

const waterOption = computed(() => {
  const d = dash.value;
  const b = blockOf('water');
  if (!d || !b) return null;
  return energyCumulativeOption({
    labels: d.labels,
    usage: b.cumulative,
    usageUnit: b.unit,
    output: b.outputCumulative,
    unitUse: b.unitCumulative,
    unitUseLabel: unitUseLabel(b.unit),
    color: '#387bff',
    labelInterval: labelInterval.value
  });
});

const steamOption = computed(() => {
  const d = dash.value;
  const b = blockOf('steam');
  if (!d || !b) return null;
  return energyCumulativeOption({
    labels: d.labels,
    usage: b.cumulative,
    usageUnit: b.unit,
    output: b.outputCumulative,
    unitUse: b.unitCumulative,
    unitUseLabel: unitUseLabel(b.unit),
    color: '#bd7e00',
    labelInterval: labelInterval.value
  });
});

const compareOption = computed(() =>
  overview.value ? unitKwhCompareOption(overview.value.lineUnitKwh, overview.value.targetUnitKwh) : null
);

/** 区间覆盖度：累计值吃进了几格可信数据（中断过的区间必须看得出来） */
function coverage(b: DashboardEnergyBlock | null): string {
  const n = dash.value?.bucketCount ?? 0;
  if (!b || !n) return '';
  const covered = b.cumulativeCovered[b.cumulativeCovered.length - 1] ?? 0;
  return covered === n ? `${n} 个时段全覆盖` : `已覆盖 ${covered}/${n} 个时段`;
}

const costNote = computed(() => {
  const d = dash.value;
  if (!d) return '';
  if (d.cost.available) return '实际值 vs 每月标准变动费用';
  return d.cost.blockers.join(' · ') || '算料未齐，暂时算不出';
});
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button :loading="loading" @click="load"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
        <el-button type="primary" plain @click="notImplemented('导出看板快照')">
          <el-icon><Download /></el-icon>&nbsp;导出
        </el-button>
      </template>
    </PageHeader>

    <!-- 时间区间：整页数字都跟着它走 -->
    <el-card shadow="never" class="mb-16">
      <div class="query">
        <span class="query__label">统计区间</span>
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
          <el-radio-button value="hour">按小时</el-radio-button>
          <el-radio-button value="day">按天</el-radio-button>
        </el-radio-group>
        <el-button-group>
          <el-button v-for="q in QUICK" :key="q.label" size="small" @click="applyQuick(q)">{{ q.label }}</el-button>
        </el-button-group>
        <el-button type="primary" size="small" :loading="loading" @click="load">查询</el-button>
        <span class="query__hint">
          {{ dash ? `${dash.from} 至 ${dash.to} · ${dash.bucketCount} 个时段` : '加载中…' }}
        </span>
      </div>
    </el-card>

    <!-- 算料不齐时把原因摊开说，别只给一张空图 -->
    <el-alert
      v-if="dash && !dash.cost.available"
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="单箱变动费用暂时算不出"
      :description="dash.cost.blockers.join('；') + '。相关指标如实留空（显示 --），不拿电量反推产量凑数。'"
    />

    <!-- 区间累计指标卡（原「今日用量统计」已按 v1.4.0 改为累计口径） -->
    <div class="stat-grid">
      <StatCard
        label="区间累计用电"
        :value="big(t?.kwh ?? 0, '').v"
        :unit="big(t?.kwh ?? 0, 'kWh').u"
        color="#1664ff"
        :loading="loading"
        :foot="`折合 ${int(t?.kwh ?? 0)} kWh`"
      />
      <StatCard
        label="区间累计用水"
        :value="t?.waterT === null || t?.waterT === undefined ? '--' : t.waterT.toFixed(2)"
        unit="t"
        color="#387bff"
        :loading="loading"
        :foot="t?.waterT == null ? '车间进水总表未接入' : '车间总表，不可拆分到线别'"
      />
      <StatCard
        label="区间累计用汽"
        :value="t?.steamT === null || t?.steamT === undefined ? '--' : t.steamT.toFixed(2)"
        unit="t"
        color="#bd7e00"
        :loading="loading"
        :foot="t?.steamT == null ? '蒸汽总表本区间无数据' : '蒸汽总表（已温压补偿）'"
      />
      <StatCard
        label="区间累计产量"
        :value="big(t?.output ?? null, '').v"
        :unit="big(t?.output ?? null, '瓶').u"
        color="#86909c"
        :loading="loading"
        :foot="dash && !dash.productionAvailable ? '产量数据源未接入，统一显示 --' : '来自 MES / 计数器报工'"
      />
      <StatCard
        label="单箱变动费用"
        :value="perCaseText"
        unit="元/箱"
        color="#2a814b"
        :loading="loading"
        :foot="dash?.cost.standardPerCase ? `标准 ${dash.cost.standardPerCase} 元/箱` : '标准值未标定，暂不比对'"
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
        label="数据上报完整率"
        :value="(kpi?.dataQualityRate ?? 0).toFixed(1)"
        unit="%"
        color="#bd7e00"
        :loading="loading"
        foot="各产线整点应报点位的实收比例"
      />
    </div>

    <!-- 主趋势图：单箱变动费用 vs 每月标准变动费用 -->
    <div class="card">
      <div class="card__head">
        <h3 class="card__title">单箱变动费用趋势</h3>
        <span class="card__extra">{{ costNote }}</span>
      </div>
      <el-skeleton v-if="loading" :rows="6" animated />
      <ChartPanel v-else title="" :option="costOption" :height="300" class="chart-inner" />
    </div>

    <!-- 三张按能源分的累计趋势图：电为主，水/汽为辅 -->
    <div class="card mt-16">
      <div class="card__head">
        <h3 class="card__title">电力累计用量 · 产量 · 单耗</h3>
        <span class="card__extra">{{ coverage(dash?.energies.electric ?? null) }}</span>
      </div>
      <el-skeleton v-if="loading" :rows="7" animated />
      <ChartPanel v-else title="" :option="electricOption" :height="320" class="chart-inner" />
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card__head">
          <h3 class="card__title">水累计用量 · 产量 · 单耗</h3>
          <span class="card__extra">车间总表 · {{ coverage(dash?.energies.water ?? null) }}</span>
        </div>
        <el-skeleton v-if="loading" :rows="5" animated />
        <ChartPanel v-else title="" :option="waterOption" :height="260" class="chart-inner" />
      </div>

      <div class="card">
        <div class="card__head">
          <h3 class="card__title">汽累计用量 · 产量 · 单耗</h3>
          <span class="card__extra">车间总表 · {{ coverage(dash?.energies.steam ?? null) }}</span>
        </div>
        <el-skeleton v-if="loading" :rows="5" animated />
        <ChartPanel v-else title="" :option="steamOption" :height="260" class="chart-inner" />
      </div>
    </div>

    <!-- 对标 + 最新告警 -->
    <div class="grid-2">
      <div class="card">
        <div class="card__head">
          <h3 class="card__title">各产线单位电耗对标</h3>
          <span class="card__extra">{{ overview?.targetUnitKwh == null ? '标准未标定' : `车间目标 ${overview.targetUnitKwh.toFixed(2)} kWh/千瓶` }}</span>
        </div>
        <ChartPanel v-if="!loading" title="" :option="compareOption" :height="260" class="chart-inner" />
        <el-skeleton v-else :rows="5" animated />
      </div>

      <div class="card">
        <div class="card__head">
          <h3 class="card__title">最新告警</h3>
          <el-button link type="primary" @click="$router.push('/alarm/realtime')">
            查看全部<el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
        <el-table v-loading="loading" :data="overview?.latestAlarms ?? []" size="default">
          <el-table-column prop="code" label="告警编号" min-width="150">
            <template #default="{ row }"><span class="t-mono">{{ row.code }}</span></template>
          </el-table-column>
          <el-table-column label="级别" width="80">
            <template #default="{ row }"><AlarmLevelTag :level="row.level" /></template>
          </el-table-column>
          <el-table-column prop="content" label="内容" min-width="220" show-overflow-tooltip />
          <el-table-column prop="occurredAt" label="发生时间" width="170">
            <template #default="{ row }"><span class="t-mono">{{ row.occurredAt }}</span></template>
          </el-table-column>
          <template #empty>
            <el-empty description="告警引擎未接入，暂无数据" :image-size="60" />
          </template>
        </el-table>
      </div>
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

/**
 * ⚠ 并排的两张卡不能吃全局 `.card + .card { margin-top: 16px }`（index.scss）。
 *   那条规则是给「同一列里上下堆叠」准备的；在 grid 里第二张卡与第一张是**左右**相邻，
 *   照样命中相邻兄弟选择器，于是整体下沉 16px、高度又少 16px ——
 *   表现就是「汽累计用量 / 最新告警」的上下边和左侧对不齐（v1.4.4 修复）。
 */
.grid-2 > .card + .card { margin-top: 0; }

.card + .card { margin-top: 16px; }
.mt-16 { margin-top: 16px; }
.mb-16 { margin-bottom: 16px; }

/* 内嵌图表卡：去掉标题栏留白，避免卡片套卡片 */
.chart-inner {
  border: none;
  padding: 0;

  :deep(.card__head) { display: none; }
}

.query {
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
  margin-left: auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-family: var(--el-font-family-mono, monospace);
}
</style>
