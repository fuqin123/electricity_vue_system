<script setup lang="ts">
/**
 * GatewayView —— 采集网关
 * 网关在线状态与心跳监控。网关离线会直接造成整片数据缺失，
 * 因此该页是大屏「数据上报质量」异常的排查起点。
 */
import { computed, onMounted, ref } from 'vue';
import { adminApi, type Gateway } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { notImplemented } from '@/utils/pending';

const list = ref<Gateway[]>([]);
const loading = ref(true);
const status = ref('');

onMounted(async () => {
  try {
    list.value = await adminApi.getGateways();
  } finally {
    loading.value = false;
  }
});

const online = computed(() => list.value.filter((g) => g.status === 'online').length);
const pointTotal = computed(() => list.value.reduce((a, g) => a + g.pointCount, 0));

const columns: TableColumn[] = [
  { prop: 'name', label: '网关名称', minWidth: 170 },
  { prop: 'ip', label: 'IP 地址', width: 140, mono: true },
  { prop: 'port', label: '端口', width: 90, mono: true },
  { prop: 'protocol', label: '协议', width: 120 },
  { prop: 'pointCount', label: '点位数', width: 90, mono: true },
  { prop: 'status', label: '状态', width: 100, slot: 'col-status' },
  { prop: 'lastHeartbeat', label: '最后心跳', width: 180, mono: true },
  { prop: 'id', label: '操作', width: 160, slot: 'col-op' }
];

function fetchGateways(query: { page: number; pageSize: number; keyword?: string }) {
  const kw = String(query.keyword ?? '').toLowerCase();
  let rows = list.value;
  if (status.value) rows = rows.filter((g) => g.status === status.value);
  if (kw) rows = rows.filter((g) => `${g.name}${g.ip}${g.protocol}`.toLowerCase().includes(kw));
  return Promise.resolve({ list: rows, total: rows.length, page: 1, pageSize: 20 });
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="notImplemented('重新下发采集配置')"><el-icon><Refresh /></el-icon>&nbsp;下发配置</el-button>
        <el-button type="primary" @click="notImplemented('新增网关')"><el-icon><Plus /></el-icon>&nbsp;新增网关</el-button>
      </template>
    </PageHeader>

    <div class="stat-grid">
      <StatCard label="在线网关" :value="`${online} / ${list.length}`" color="#2a814b" :loading="loading" :foot="`离线 ${list.length - online} 台`" />
      <StatCard label="接入点位总数" :value="pointTotal" unit="个" color="#1664ff" :loading="loading" />
      <StatCard label="采集周期" :value="60" unit="秒" color="#387bff" :loading="loading" foot="整点聚合前的原始采样周期" />
      <StatCard label="离线告警规则" value="心跳 > 5 分钟" color="#d7312a" :loading="loading" foot="触发严重告警并通知值班人员" />
    </div>

    <TablePage :columns="columns" :fetch="fetchGateways" keyword-placeholder="网关名称 / IP / 协议" :max-height="480">
      <template #filters>
        <el-select v-model="status" placeholder="全部状态" clearable style="width: 130px">
          <el-option label="在线" value="online" />
          <el-option label="离线" value="offline" />
        </el-select>
      </template>

      <template #col-status="{ row }">
        <el-tag :type="row.status === 'online' ? 'success' : 'danger'" size="small">
          {{ row.status === 'online' ? '在线' : '离线' }}
        </el-tag>
      </template>

      <template #col-op="{ row }">
        <el-button link type="primary" @click="notImplemented(`测试连接 ${row.name}`)">测试连接</el-button>
        <el-button link type="primary" @click="notImplemented(`编辑 ${row.name}`)">编辑</el-button>
      </template>
    </TablePage>
  </div>
</template>

<style scoped lang="scss">
.stat-grid { margin-bottom: 16px; }
</style>
