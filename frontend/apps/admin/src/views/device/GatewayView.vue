<script setup lang="ts">
/**
 * GatewayView —— 采集网关（IP、端口、协议的配置入口）
 *
 * 网关离线会直接造成整片数据缺失，因此这一页是大屏「数据上报质量」异常的排查起点：
 *   · 先看状态与最后心跳 —— 心跳超过**一个采集周期**（现场为小时级，阈值默认 90 分钟）
 *     即显示离线。⚠ 阈值必须跟采集节奏走：按分钟级直觉设 5 分钟会让全部网关
 *     在近乎所有时间显示离线（v1.4.4 修过的现场问题，见后端 gateway_offline_after）；
 *   · 点「测试连接」—— 对旗下每个点位试读一次，返回真实读数，
 *     一次性区分「网络不通」「从站号写错」「寄存器地址写错」三种情况。
 *
 * ⚠ 网关记录一旦在后台改动过就会标记 `source = 后台维护`，
 *   此后 `manage.py seed_registry` 默认不再覆盖它（需要覆盖请加 --force）。
 *   这是为了避免「现场在后台调好了、下一次同步又被改回配置文件里的旧值」。
 */
import { computed, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { adminApi, type Gateway } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import StatCard from '@/components/StatCard.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { useSourceBanner } from '@/utils/source';

const banner = useSourceBanner('admin.gateways');
const list = ref<Gateway[]>([]);
const loading = ref(true);
const status = ref('');
/** 列表是否已取回；配合 seq 让 TablePage 在取数完成后重新取一次（见 extraQuery） */
const ready = ref(false);
const seq = ref(0);

/**
 * ⚠ TablePage 只在「挂载」与「extraQuery 变化」时取数，而本页数据是异步来的：
 *   首屏 TablePage 先挂载并取了一次数（那时 list 还是空的），等接口回来
 *   list 已经填好、表格却不会再取 —— 现场看到的就是「网关必须刷新才显示」（B-519 同款）。
 *   把「就绪 / 状态筛选 / 取数批次」一起并入 extraQuery，数据变了表格自己会跟上。
 */
const extraQuery = computed(() => ({ ready: ready.value, status: status.value, seq: seq.value }));

async function refresh() {
  loading.value = true;
  try {
    list.value = await adminApi.getGateways();
    ready.value = true;
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    loading.value = false;
    seq.value += 1;
  }
}
void refresh();

const online = computed(() => list.value.filter((g) => g.status === 'online').length);
const pointTotal = computed(() => list.value.reduce((a, g) => a + g.pointCount, 0));

const columns: TableColumn<Gateway>[] = [
  { prop: 'code', label: '网关编号', width: 130 },
  { prop: 'name', label: '网关名称', minWidth: 190 },
  { prop: 'endpoint', label: '地址（IP:端口 / 接口根地址）', minWidth: 220, mono: true },
  { prop: 'protocol', label: '协议', width: 170 },
  { prop: 'pointCount', label: '点位数', width: 84, mono: true },
  { prop: 'status', label: '状态', width: 86, slot: 'col-status' },
  { prop: 'lastHeartbeat', label: '最后心跳', width: 168, mono: true },
  { prop: 'source', label: '配置来源', width: 100, slot: 'col-source' }
];

function fetchGateways(query: { page: number; pageSize: number; keyword?: string }) {
  const kw = String(query.keyword ?? '').toLowerCase();
  let rows = list.value;
  if (status.value) rows = rows.filter((g) => g.status === status.value);
  if (kw) rows = rows.filter((g) => `${g.code}${g.name}${g.endpoint}${g.protocol}`.toLowerCase().includes(kw));
  return Promise.resolve({ list: rows, total: rows.length, page: 1, pageSize: 50 });
}

/* ── 表单 ─────────────────────────────────────────────── */
const drawer = ref(false);
const saving = ref(false);
const editingId = ref<number | null>(null);

function blankForm() {
  return {
    code: '',
    name: '',
    kind: 'modbus_rtu_tcp' as Gateway['kind'],
    host: '',
    port: 8891 as number | null,
    baseUrl: '',
    timeoutMs: 1500,
    intervalSeconds: 60,
    enabled: true,
    remark: ''
  };
}
const form = reactive(blankForm());
const isModbus = computed(() => form.kind === 'modbus_rtu_tcp');

function openCreate() {
  Object.assign(form, blankForm());
  editingId.value = null;
  drawer.value = true;
}

function openEdit(g: Gateway) {
  editingId.value = g.id;
  Object.assign(form, {
    code: g.code,
    name: g.name,
    kind: g.kind,
    host: g.ip,
    port: g.port,
    baseUrl: g.baseUrl,
    timeoutMs: g.timeoutMs,
    intervalSeconds: g.intervalSeconds,
    enabled: g.enabled,
    remark: g.remark
  });
  drawer.value = true;
}

async function submit() {
  if (!form.code.trim() || !form.name.trim()) {
    ElMessage.warning('网关编号与名称为必填项');
    return;
  }
  if (isModbus.value && (!form.host.trim() || !form.port)) {
    ElMessage.warning('串口服务器必须填 IP 与端口');
    return;
  }
  if (!isModbus.value && !form.baseUrl.trim()) {
    ElMessage.warning('HTTP API 类型必须填接口根地址');
    return;
  }
  saving.value = true;
  try {
    await adminApi.saveGateway(
      {
        code: form.code.trim(),
        name: form.name.trim(),
        kind: form.kind,
        ip: form.host.trim(),
        port: form.port,
        baseUrl: form.baseUrl.trim(),
        timeoutMs: form.timeoutMs,
        intervalSeconds: form.intervalSeconds,
        enabled: form.enabled,
        remark: form.remark
      },
      editingId.value ?? undefined
    );
    ElMessage.success('已保存');
    drawer.value = false;
    await refresh();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    saving.value = false;
  }
}

async function doDelete(g: Gateway) {
  try {
    await ElMessageBox.confirm(`删除网关「${g.code} ${g.name}」？`, '删除确认', {
      type: 'warning',
      confirmButtonText: '确认删除',
      cancelButtonText: '取消'
    });
  } catch {
    return;
  }
  try {
    await adminApi.deleteGateway(g.id);
    ElMessage.success('已删除');
    await refresh();
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

/* ── 连接测试 ─────────────────────────────────────────── */
const testing = ref<number | null>(null);
const testVisible = ref(false);
const testResult = ref({
  gateway: '',
  reachable: false,
  detail: '',
  durationMs: 0,
  items: [] as { meter: string; name: string; ok: boolean; value: number | null; raw: string; error: string }[]
});

async function doTest(g: Gateway) {
  testing.value = g.id;
  try {
    const res = await adminApi.testGateway(g.id);
    testResult.value = {
      gateway: `${g.code} ${g.name}`,
      reachable: res.reachable,
      detail: res.detail,
      durationMs: res.durationMs,
      items: (res.items ?? []).map((it) => ({
        meter: it.meter ?? '',
        name: it.name ?? '',
        ok: it.ok,
        value: it.value,
        raw: it.raw,
        error: it.error
      }))
    };
    if (res.reachable) await refresh();
  } catch (e) {
    testResult.value = {
      gateway: g.code,
      reachable: false,
      detail: (e as Error).message,
      durationMs: 0,
      items: []
    };
  } finally {
    testing.value = null;
    testVisible.value = true;
    // ⚠ 无论成败都要刷新：测试结果会写成网关的「成功 / 失败证据」（B-542），
    //   失败同样会把状态翻成「离线」。旧代码写成 `if (res.reachable) refresh()`，
    //   于是测试失败的网关点了也没反应，看起来像「状态没有更新」。
    await refresh();
  }
}
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="refresh"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
        <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新增网关</el-button>
      </template>
    </PageHeader>

    <el-alert v-if="banner.show" type="warning" :closable="false" show-icon class="mb-16" :title="banner.text" />

    <div class="stat-grid">
      <StatCard
        label="在线网关"
        :value="`${online} / ${list.length}`"
        color="#2a814b"
        :loading="loading"
        :foot="`离线 ${list.length - online} 台`"
      />
      <StatCard label="接入点位总数" :value="pointTotal" unit="个" color="#1664ff" :loading="loading" />
      <StatCard
        label="采样周期"
        :value="list[0]?.intervalSeconds ?? 60"
        unit="秒"
        color="#387bff"
        :loading="loading"
        foot="整点聚合前的原始采样周期"
      />
      <StatCard
        label="离线判定"
        value="心跳 > 1 个采集周期"
        color="#d7312a"
        :loading="loading"
        foot="现场按小时采集，阈值默认 90 分钟（可配 collect.gateway_offline_minutes）"
      />
    </div>

    <TablePage
      :columns="columns"
      :fetch="fetchGateways"
      :extra-query="extraQuery"
      keyword-placeholder="网关编号 / 名称 / 地址"
      :op-width="210"
      :max-height="460"
    >
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

      <template #col-source="{ row }">
        <el-tag :type="row.source === 'admin' ? 'warning' : 'info'" size="small" effect="plain">
          {{ row.source === 'admin' ? '后台维护' : '配置文件' }}
        </el-tag>
      </template>

      <template #op="{ row }">
        <el-button link type="primary" :loading="testing === row.id" @click="doTest(row)">测试连接</el-button>
        <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
        <el-button link type="danger" @click="doDelete(row)">删除</el-button>
      </template>
    </TablePage>

    <!-- ══════ 新增 / 编辑 ══════ -->
    <el-drawer v-model="drawer" :title="editingId ? '编辑网关' : '新增网关'" size="560px">
      <el-form label-width="120px" label-position="right">
        <el-form-item label="网关编号" required>
          <el-input v-model="form.code" placeholder="如 GW-PDB-5，全局唯一" />
        </el-form-item>
        <el-form-item label="网关名称" required>
          <el-input v-model="form.name" placeholder="如 二期配电房串口服务器1口" />
        </el-form-item>
        <el-form-item label="网关类型">
          <el-radio-group v-model="form.kind">
            <el-radio-button value="modbus_rtu_tcp">串口服务器</el-radio-button>
            <el-radio-button value="http_api">HTTP API</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <template v-if="isModbus">
          <el-form-item label="IP 地址" required>
            <el-input v-model="form.host" placeholder="如 10.112.66.41" />
          </el-form-item>
          <el-form-item label="端口" required>
            <el-input-number v-model="form.port" :min="1" :max="65535" controls-position="right" />
            <span class="form-hint">串口服务器把 RS485 映射到的 TCP 端口</span>
          </el-form-item>
        </template>
        <template v-else>
          <el-form-item label="接口根地址" required>
            <el-input v-model="form.baseUrl" placeholder="如 http://47.111.74.100:8980" />
          </el-form-item>
        </template>

        <el-form-item label="单点超时">
          <el-input-number v-model="form.timeoutMs" :min="200" :step="100" controls-position="right" />
          <span class="form-hint">毫秒。超时预算 = 超时 ×(1+重试次数)，别超过采样周期</span>
        </el-form-item>
        <el-form-item label="采样周期">
          <el-input-number v-model="form.intervalSeconds" :min="5" :step="5" controls-position="right" />
          <span class="form-hint">秒</span>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="drawer = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-drawer>

    <!-- ══════ 连接测试结果 ══════ -->
    <el-dialog v-model="testVisible" width="720px" :title="`连接测试 · ${testResult.gateway}`">
      <el-alert
        :type="testResult.reachable ? 'success' : 'error'"
        :closable="false"
        show-icon
        :title="testResult.reachable ? '网关可达' : '网关不可达'"
        :description="testResult.detail"
        class="mb-16"
      />
      <el-table v-if="testResult.items.length" :data="testResult.items" size="small" max-height="360">
        <el-table-column prop="meter" label="点位" width="120" />
        <el-table-column prop="name" label="名称" min-width="150" show-overflow-tooltip />
        <el-table-column label="结果" width="84">
          <template #default="{ row }">
            <el-tag :type="row.ok ? 'success' : 'danger'" size="small">{{ row.ok ? '成功' : '失败' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="读数" width="130" align="right">
          <template #default="{ row }">
            <span class="t-mono">{{ row.value ?? '--' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="原始报文 / 错误" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="t-mono">{{ row.ok ? row.raw : row.error }}</span>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button type="primary" @click="testVisible = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.stat-grid {
  margin-bottom: 16px;
}
.form-hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
