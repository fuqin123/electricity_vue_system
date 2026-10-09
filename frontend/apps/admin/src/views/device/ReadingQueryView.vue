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
 *
 * v1.4.4 新增：每行一个「读取当前值」按钮 —— 直接向现场设备要一次当前读数，
 * 结果只在一个弹窗里展示，**不写数据库、不写日志、不更新点位的最近读数**
 * （与「计量点位 → 试采」的区别就在这里，见 `adminApi.readMeterCurrent` 的说明）。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import {
  adminApi,
  type Gateway,
  type MeterCurrentResult,
  type MeterPoint,
  type MeterReading,
  type ReadingQuery
} from '@eep/shared';
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

/* ── 读取当前值（不落库）───────────────────────────────── */
/**
 * 弹窗里显示的是**刚从设备读回来**的一次读数，与上面的历史读数表无关：
 * 它不出现在 `energy_raw`，也不改点位的 `last_value / last_at / last_ok`。
 *
 * 为什么要这样设计：`last_ok / last_at` 是采集链路的健康信号，本页的排障完全依赖它。
 * 如果「看一眼当前值」顺手把点位刷成刚刚成功，断讯的表就会显示在线，
 * 之后谁也说不清是链路恢复了还是有人点过按钮。
 */
const probe = reactive({
  visible: false,
  loading: false,
  meterId: 0,
  label: '',
  /** 超时 / 重试：留空用网关与采集配置里的值，可在弹窗里临时调，用来分辨「偶发还是必现」 */
  timeoutMs: null as number | null,
  retryTimes: null as number | null,
  result: null as MeterCurrentResult | null
});

/** 打开弹窗并立刻读一次；`again=true` 表示在同一弹窗里再读一次 */
async function readCurrent(row?: MeterReading, again = false) {
  if (row) {
    probe.meterId = row.meterId;
    probe.label = `${row.meter} ${row.meterName}`;
    probe.timeoutMs = null;
    probe.retryTimes = null;
    probe.result = null;
    probe.visible = true;
  }
  if (!probe.meterId) return;
  probe.loading = true;
  try {
    probe.result = await adminApi.readMeterCurrent(probe.meterId, {
      timeoutMs: probe.timeoutMs ?? undefined,
      retryTimes: probe.retryTimes ?? undefined
    });
    if (!again) ElMessage.success('已从设备读取当前值（未落库）');
  } catch (e) {
    ElMessage.error((e as Error).message);
    probe.result = null;
  } finally {
    probe.loading = false;
  }
}

/** 弹窗里展示的读数（大字） */
const probeValueText = computed(() => {
  const r = probe.result;
  if (!r) return '--';
  return r.value === null ? '--' : r.value.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
});

/** API 类网关会带回瞬时流量 / 温度 / 压力等，排障时有据可查 */
const probeExtra = computed(() => Object.entries(probe.result?.extra ?? {}));

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

    <el-alert
      type="success"
      :closable="false"
      show-icon
      class="mb-16"
      title="「读取当前值」= 现场实读一次，不留痕"
      description="每行的按钮会直接向现场设备要一次当前读数，结果只在这个弹窗里展示：不写数据库、不写日志、也不更新点位的「最近读数 / 最近状态」。所以它不会把断讯的表「治好」——历史读数与采集日志仍由后台采集任务负责。"
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
      :op-width="120"
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
      <template #op="{ row }">
        <el-button link type="primary" :loading="probe.loading && probe.meterId === row.meterId" @click="readCurrent(row)">
          读取当前值
        </el-button>
      </template>
      <template #toolbar>
        <el-button size="small" plain @click="exportCsv">
          <el-icon><Download /></el-icon>&nbsp;导出当前页
        </el-button>
      </template>
    </TablePage>

    <!-- ══════ 读取当前值（不落库）══════ -->
    <el-dialog v-model="probe.visible" width="720px" :title="`读取当前值 · ${probe.label}`">
      <div class="probe__bar">
        <span class="probe__label">超时</span>
        <el-input-number v-model="probe.timeoutMs" :min="200" :max="10000" :step="100" controls-position="right" style="width: 130px" />
        <span class="probe__unit">ms</span>
        <span class="probe__label">重试</span>
        <el-input-number v-model="probe.retryTimes" :min="0" :max="5" controls-position="right" style="width: 110px" />
        <span class="probe__unit">次</span>
        <el-tooltip content="留空表示用网关配置的超时与采集配置的重试次数；改完点「再读一次」即生效" placement="top">
          <span class="probe__hint">留空 = 用后端配置</span>
        </el-tooltip>
        <el-button type="primary" size="small" :loading="probe.loading" @click="readCurrent(undefined, true)">再读一次</el-button>
      </div>

      <el-skeleton v-if="probe.loading && !probe.result" :rows="5" animated />

      <template v-else-if="probe.result">
        <el-alert
          :type="probe.result.ok ? 'success' : 'error'"
          :closable="false"
          show-icon
          class="mb-16"
          :title="probe.result.ok ? '读取成功' : '读取失败'"
          :description="
            probe.result.ok
              ? `本次尝试 ${probe.result.attempts} 次，耗时 ${probe.result.durationMs} ms`
              : probe.result.error || '未知错误'
          "
        />

        <el-alert
          v-if="probe.result.enabled === false"
          type="warning"
          :closable="false"
          show-icon
          class="mb-16"
          title="该点位当前是「停用」状态"
          description="手动读取不受启用状态限制（排障本来就要读停用的表）。但后台采集任务不会采它，因此上面的历史读数里不会有它的新数据。"
        />

        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="当前读数">
            <span class="probe__value" :class="{ 'is-missing': probe.result.value === null }">{{ probeValueText }}</span>
            <span class="probe__unit">{{ probe.result.unit || '' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="读取时刻">{{ probe.result.at }}</el-descriptions-item>
          <el-descriptions-item label="仪表">{{ probe.result.meter }} {{ probe.result.name }}</el-descriptions-item>
          <el-descriptions-item label="能源 / 归属">
            {{ probe.result.category || '—' }} · {{ probe.result.lineCode || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="网关">
            {{ probe.result.gatewayCode }} {{ probe.result.gatewayName }}
          </el-descriptions-item>
          <el-descriptions-item label="协议">{{ probe.result.protocol || '—' }}</el-descriptions-item>
          <el-descriptions-item label="读取方式" :span="2">
            <span class="t-mono">{{ probe.result.address || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="倍率">{{ probe.result.factor ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="耗时 / 尝试">
            {{ probe.result.durationMs }} ms / {{ probe.result.attempts }} 次
          </el-descriptions-item>
          <el-descriptions-item label="超时 / 重试" :span="2">
            {{ probe.result.timeoutMs }} ms ×（1 + {{ probe.result.retryTimes }} 次重试）＝ 最长约
            {{ probe.result.timeoutMs * (1 + probe.result.retryTimes) }} ms
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="probeExtra.length" class="probe__extra">
          <div class="probe__extra-title">设备同帧带回的其它字段</div>
          <div v-for="[k, v] in probeExtra" :key="k" class="probe__extra-row">
            <span class="probe__extra-key t-mono">{{ k }}</span>
            <span class="t-mono">{{ v }}</span>
          </div>
        </div>

        <div class="probe__raw">
          <div class="probe__raw-title">{{ probe.result.ok ? '原始报文 / 响应' : '错误信息' }}</div>
          <pre class="probe__raw-body t-mono">{{ probe.result.ok ? probe.result.raw || '—' : probe.result.error || '—' }}</pre>
        </div>
      </template>

      <template #footer>
        <span class="probe__footer-note">
          本次读取不写数据库、不写日志，也不更新点位的最近读数（persisted={{ probe.result?.persisted ?? false }}）
        </span>
        <el-button @click="probe.visible = false">关闭</el-button>
      </template>
    </el-dialog>
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

/* ── 读取当前值弹窗 ───────────────────────────────────── */
.probe__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.probe__label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.probe__unit {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.probe__hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  margin-left: auto;
}
.probe__value {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 22px;
  font-weight: 600;
  color: var(--yl-primary);
  margin-right: 6px;
}
.probe__extra {
  margin-top: 16px;
}
.probe__extra-title,
.probe__raw-title {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.probe__extra-row {
  display: flex;
  gap: 10px;
  font-size: 12px;
  line-height: 20px;
}
.probe__extra-key {
  color: var(--el-text-color-placeholder);
  min-width: 120px;
}
.probe__raw {
  margin-top: 16px;
}
.probe__raw-body {
  margin: 0;
  padding: 10px 12px;
  background: var(--yl-surface-container);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  font-size: 12px;
  line-height: 18px;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 160px;
  overflow-y: auto;
}
.probe__footer-note {
  float: left;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 32px;
}
</style>
