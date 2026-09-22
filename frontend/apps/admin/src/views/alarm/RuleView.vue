<script setup lang="ts">
/**
 * RuleView —— 告警规则
 * 阈值与触发条件的配置入口。规则改动会直接影响大屏与后台的告警判定，
 * 因此每次变更都必须写入操作审计日志。
 */
import { onMounted, ref } from 'vue';
import { adminApi, energyModel, thresholds, type PageQuery, type PageResult, type SysParam } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

interface RuleRow {
  id: number;
  code: string;
  name: string;
  target: string;
  condition: string;
  level: string;
  levelType: 'danger' | 'warning' | 'info';
  enabled: boolean;
  updatedAt: string;
}

const params = ref<SysParam[]>([]);

onMounted(async () => {
  const res = await adminApi.getParams({ page: 1, pageSize: 100 });
  params.value = res.list;
});

/** 规则以「系统参数」为数据源展示，保证阈值只有一个真值来源 */
const rows = ref<RuleRow[]>([
  { id: 1, code: 'R-UNIT-KWH-HIGH', name: '单位电耗超上限', target: '产线 / 车间', condition: `单位电耗 > ${thresholds.unitKwhHigh.toFixed(2)} kWh/千瓶`, level: '严重', levelType: 'danger', enabled: true, updatedAt: '2026-03-12 10:22:41' },
  { id: 2, code: 'R-OUTPUT-LOW', name: '本小时低产', target: '产线', condition: `整点预期产量 < 额定的 ${Math.round(thresholds.outputLowRate * 100)}%`, level: '警告', levelType: 'warning', enabled: true, updatedAt: '2026-03-12 10:24:08' },
  { id: 3, code: 'R-ZERO-OUTPUT', name: '整点无产量', target: '产线', condition: '整点产量 = 0 且电表有读数', level: '警告', levelType: 'warning', enabled: true, updatedAt: '2026-03-12 10:25:37' },
  { id: 4, code: 'R-METER-MISS', name: '电量应报未报', target: '计量点位', condition: '产量正常但电量缺失', level: '严重', levelType: 'danger', enabled: true, updatedAt: '2026-03-12 10:26:55' },
  { id: 5, code: 'R-STEAM-DIFF', name: '汽耗环比异常', target: '车间总表', condition: '较昨日同期波动 > ±10%', level: '警告', levelType: 'warning', enabled: true, updatedAt: '2026-04-02 16:41:12' },
  { id: 6, code: 'R-WATER-DIFF', name: '水耗环比异常', target: '车间总表', condition: '较昨日同期波动 > ±12%', level: '提示', levelType: 'info', enabled: true, updatedAt: '2026-04-02 16:42:30' },
  { id: 7, code: 'R-GATEWAY-OFFLINE', name: '采集网关离线', target: '网关', condition: '心跳超时 > 5 分钟', level: '严重', levelType: 'danger', enabled: true, updatedAt: '2026-05-19 09:03:44' },
  { id: 8, code: 'R-TARGET-DEV', name: '单位电耗偏离目标', target: '车间合计', condition: `单位电耗 > 目标 ${energyModel.targetUnitKwh.toFixed(2)} 的 105%`, level: '提示', levelType: 'info', enabled: false, updatedAt: '2026-06-08 14:19:02' }
]);

const columns: TableColumn[] = [
  { prop: 'code', label: '规则编码', width: 180 },
  { prop: 'name', label: '规则名称', width: 170 },
  { prop: 'target', label: '作用对象', width: 130 },
  { prop: 'condition', label: '触发条件', minWidth: 300 },
  { prop: 'level', label: '告警级别', width: 100, slot: 'col-level' },
  { prop: 'enabled', label: '状态', width: 90, slot: 'col-enabled' },
  { prop: 'updatedAt', label: '最近修改', width: 180, mono: true },
  { prop: 'id', label: '操作', width: 130, slot: 'col-op' }
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

function toggle(row: RuleRow) {
  row.enabled = !row.enabled;
  notImplemented(row.enabled ? `启用规则 ${row.code}` : `停用规则 ${row.code}`);
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" @click="notImplemented('新增告警规则')">
          <el-icon><Plus /></el-icon>&nbsp;新增规则
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="mb-16"
      title="阈值唯一真值来源"
      description="规则中的阈值必须与「系统管理 → 系统参数」保持一致，避免出现两套口径。修改阈值需记录审计日志并通知能源管理员。"
    />

    <TablePage :columns="columns" :fetch="fetchRules" keyword-placeholder="规则编码 / 名称 / 条件" :max-height="520">
      <template #col-level="{ row }">
        <el-tag :type="row.levelType" size="small">{{ row.level }}</el-tag>
      </template>

      <template #col-enabled="{ row }">
        <el-switch :model-value="row.enabled" size="small" @change="toggle(row)" />
      </template>

      <template #col-op="{ row }">
        <el-button link type="primary" @click="notImplemented(`编辑规则 ${row.code}`)">编辑</el-button>
        <el-button link type="primary" @click="notImplemented(`删除规则 ${row.code}`)">删除</el-button>
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
