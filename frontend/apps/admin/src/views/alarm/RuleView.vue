<script setup lang="ts">
/**
 * RuleView —— 告警规则
 * 阈值与触发条件的配置入口。规则改动会直接影响大屏与后台的告警判定，
 * 因此每次变更都必须写入操作审计日志。
 */
import { onMounted, ref } from 'vue';
import { adminApi, thresholds, type PageQuery, type PageResult, type SysParam } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';

interface RuleRow {
  id: number;
  code: string;
  name: string;
  target: string;
  condition: string;
  level: string;
  levelType: 'danger' | 'warning' | 'info';
  enabled: boolean;
  /** 规则实现位置 —— 这一页只如实陈述系统在看什么，不提供在线编辑 */
  impl: string;
}

const params = ref<SysParam[]>([]);

onMounted(async () => {
  const res = await adminApi.getParams({ page: 1, pageSize: 100 });
  params.value = res.list;
});

/**
 * 与后端告警引擎一一对应（`backend/apps/energy/services/alerts.py`）。
 *
 * ⚠ 这一页此前列的 8 条规则里，单位电耗超上限 / 低产 / 无产量 / 电量应报未报 /
 *   汽耗环比 / 水耗环比**一条都没有实现**（它们全依赖产量），而真正在跑的 6 条
 *   一个都没写 —— 这一页等于在谎报系统正在监控什么。
 *   新增规则时请同时改这里与引擎，否则现场会以为已经在告警。
 */
const rows = ref<RuleRow[]>([
  { id: 1, code: 'R-METER-UNCONNECTED', name: '计量点未接入', target: '计量点位', condition: '已登记并启用，但历史上从未采到可信读数（不参与合计，故必须显式报出）', level: '重要', levelType: 'warning', enabled: true, impl: 'alerts._rule_unconnected' },
  { id: 2, code: 'R-METER-FAIL', name: '计量点采集失败', target: '计量点位', condition: '最近一轮采集失败（last_ok=false），并带出错误原因与最近成功时刻', level: '严重', levelType: 'danger', enabled: true, impl: 'alerts._rule_collect_fail' },
  { id: 3, code: 'R-GATEWAY-FAIL', name: '网关整轮采集失败', target: '网关', condition: '该网关最近一轮的点位全部失败（0/N）。注意：这与网关列表页的「在线」徽标是两件事', level: '严重', levelType: 'danger', enabled: true, impl: 'alerts._rule_gateway' },
  { id: 4, code: 'R-COLLECT-STALE', name: '采集停摆', target: '采集服务', condition: '最新成功读数超过 collect.stale_alert_hours 未更新；或尚无任何采集数据。由大屏读路径兜底触发', level: '严重', levelType: 'danger', enabled: true, impl: 'alerts._rule_stale' },
  { id: 5, code: 'R-HOUR-QUALITY', name: '小时数据不可信', target: '计量点位', condition: '近 24 小时内存在累计值回退（疑似换表清零）或异常尖峰的小时，按点位合并为一条；缺数由覆盖率/复核规则覆盖', level: '次要', levelType: 'info', enabled: true, impl: 'alerts._rule_hour_quality' },
  { id: 6, code: 'R-COVERAGE', name: '周期覆盖率不足', target: '日汇总', condition: '上一生产日日汇总 covered_count < expected_count，即该日合计少算了若干小时', level: '次要', levelType: 'info', enabled: true, impl: 'alerts._rule_coverage' },
  { id: 11, code: 'R-HOUR-DEVIATION', name: '异常时段待复核', target: '上一生产日', condition: '端点/区间时长偏差超过「系统参数 → 数据质量 → 异常时段允许偏差」，在复核时刻生成；需人工确认，不自动恢复', level: '次要', levelType: 'info', enabled: true, impl: 'alerts._rule_hour_deviation' },
  // ── 以下依赖产量，产量侧接入后才启用（接口与表结构已就位）──
  { id: 7, code: 'R-UNIT-KWH-HIGH', name: '单位电耗超上限', target: '产线 / 车间', condition: `单位电耗 > ${thresholds.unitKwhHigh.toFixed(2)} kWh/千瓶`, level: '严重', levelType: 'danger', enabled: false, impl: '待产量接入' },
  { id: 8, code: 'R-OUTPUT-LOW', name: '本小时低产', target: '产线', condition: `整点预期产量 < 额定的 ${Math.round(thresholds.outputLowRate * 100)}%`, level: '警告', levelType: 'warning', enabled: false, impl: '待产量接入' },
  { id: 9, code: 'R-ZERO-OUTPUT', name: '整点无产量', target: '产线', condition: '整点产量 = 0 且电表有读数', level: '警告', levelType: 'warning', enabled: false, impl: '待产量接入' },
  { id: 10, code: 'R-TARGET-DEV', name: '单位电耗偏离目标', target: '车间合计', condition: '单位电耗 > 按线标准单耗的 105%', level: '提示', levelType: 'info', enabled: false, impl: '待产量接入' }
]);

const columns: TableColumn[] = [
  { prop: 'code', label: '规则编码', width: 190 },
  { prop: 'name', label: '规则名称', width: 160 },
  { prop: 'target', label: '作用对象', width: 110 },
  { prop: 'condition', label: '触发条件', minWidth: 320 },
  { prop: 'level', label: '告警级别', width: 100, slot: 'col-level' },
  { prop: 'enabled', label: '启用', width: 80, slot: 'col-enabled' },
  { prop: 'impl', label: '实现位置', width: 200, mono: true }
];

function fetchRules(query: PageQuery): Promise<PageResult<RuleRow>> {
  const kw = String(query.keyword ?? '').toLowerCase();
  const list = kw
    ? rows.value.filter((r) => `${r.code}${r.name}${r.condition}`.toLowerCase().includes(kw))
    : rows.value;
  const start = ((Number(query.page) || 1) - 1) * (Number(query.pageSize) || 10);
  const size = Number(query.pageSize) || 10;
  return Promise.resolve({ list: list.slice(start, start + size), total: list.length, page: Number(query.page) || 1, pageSize: size });
}
</script>

<template>
  <div>
    <PageHeader />

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="本页是只读对照，不是规则编辑器"
      description="告警规则由后端引擎定义（backend/apps/energy/services/alerts.py），改判定逻辑需要发版；能源单价在「能源配置管理 → 能源单价」、标准单耗在「标准单耗配置」、箱规/标准变动费用在「系统管理 → 系统参数」维护，停摆阈值在 backend/config/settings.local.yaml 的 collect.stale_alert_hours；异常数据复核时刻/允许偏差在「系统管理 → 系统参数 → 数据质量」。"
    />

    <TablePage :columns="columns" :fetch="fetchRules" keyword-placeholder="规则编码 / 名称 / 条件" :max-height="520">
      <template #col-level="{ row }">
        <el-tag :type="row.levelType" size="small">{{ row.level }}</el-tag>
      </template>

      <template #col-enabled="{ row }">
        <el-tag :type="row.enabled ? 'success' : 'info'" size="small" disable-transitions>
          {{ row.enabled ? '已启用' : '未启用' }}
        </el-tag>
      </template>
    </TablePage>

    <div class="card mt-16">
      <div class="card__head">
        <h3 class="card__title">当前生效的阈值参数</h3>
        <span class="card__extra">来自「系统管理 → 系统参数」</span>
      </div>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item v-for="p in params" :key="p.key" :label="p.name">
          <span class="t-mono">{{ p.value }}</span>
          <span class="t-subtle"> {{ p.unit }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </div>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
</style>
