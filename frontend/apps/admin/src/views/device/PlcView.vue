/**
 * PlcView —— PLC 产量采集设备（v1.8.0）
 *
 * 现场为西门子 S7-200 SMART，通过以太网 102 端口读取 DB / M / I / Q 区。
 * 目前没有 PLC 时把 `采集模式` 选成「模拟数据」即可跑通设备、点位、试读、
 * 产量绑定与小时产量整条链路；设备接入后改成「真实 PLC」并填 IP 即可。
 *
 * ⚠ 试读会更新点位的「最近读数」基准 —— 与能源试采一致，避免下一次正式采集
 *   把试读前累计的量再算一遍。
 */
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { adminApi, type PlcDevice, type PlcPoint, type PlcTestResult } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';

const devices = ref<PlcDevice[]>([]);
const loading = ref(false);
const selectedId = ref<number | null>(null);
const points = ref<PlcPoint[]>([]);
const pointsLoading = ref(false);

const selectedDevice = computed(() => devices.value.find((d) => d.id === selectedId.value) ?? null);
const onlineCount = computed(() => devices.value.filter((d) => d.status === 'online').length);
const pointTotal = computed(() => devices.value.reduce((a, d) => a + d.pointCount, 0));

async function loadDevices() {
  loading.value = true;
  try {
    devices.value = await adminApi.getPlcDevices();
    if (!selectedId.value && devices.value.length) selectedId.value = devices.value[0].id;
    if (selectedId.value && !devices.value.some((d) => d.id === selectedId.value)) {
      selectedId.value = devices.value[0]?.id ?? null;
    }
    await loadPoints();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    loading.value = false;
  }
}

async function loadPoints() {
  if (!selectedId.value) {
    points.value = [];
    return;
  }
  pointsLoading.value = true;
  try {
    points.value = await adminApi.getPlcPoints(selectedId.value);
  } catch (e) {
    ElMessage.error((e as Error).message);
    points.value = [];
  } finally {
    pointsLoading.value = false;
  }
}

function selectDevice(row: PlcDevice) {
  if (selectedId.value === row.id) return;
  selectedId.value = row.id;
  void loadPoints();
}

/* ── 设备表单 ─────────────────────────────────────────── */
const deviceDrawer = ref(false);
const deviceSaving = ref(false);
const editingDeviceId = ref<number | null>(null);

function blankDevice() {
  return {
    code: '',
    name: '',
    mode: 'simulated' as PlcDevice['mode'],
    host: '',
    port: 102,
    rack: 0,
    slot: 1,
    timeoutMs: 3000,
    intervalSeconds: 60,
    enabled: true,
    remark: ''
  };
}
const deviceForm = reactive(blankDevice());

function openDeviceCreate() {
  Object.assign(deviceForm, blankDevice());
  editingDeviceId.value = null;
  deviceDrawer.value = true;
}

function openDeviceEdit(d: PlcDevice) {
  editingDeviceId.value = d.id;
  Object.assign(deviceForm, {
    code: d.code,
    name: d.name,
    mode: d.mode,
    host: d.host,
    port: d.port,
    rack: d.rack,
    slot: d.slot,
    timeoutMs: d.timeoutMs,
    intervalSeconds: d.intervalSeconds,
    enabled: d.enabled,
    remark: d.remark
  });
  deviceDrawer.value = true;
}

async function saveDevice() {
  if (!deviceForm.code.trim() || !deviceForm.name.trim()) {
    ElMessage.warning('设备编号和名称不能为空');
    return;
  }
  if (deviceForm.mode === 'snap7' && !deviceForm.host.trim()) {
    ElMessage.warning('真实 PLC 模式必须填写 IP');
    return;
  }
  deviceSaving.value = true;
  try {
    const payload = { ...deviceForm, code: deviceForm.code.trim().toUpperCase() };
    if (editingDeviceId.value) {
      await adminApi.savePlcDevice(editingDeviceId.value, payload);
      ElMessage.success('PLC 设备已保存');
    } else {
      const res = await adminApi.createPlcDevice(payload);
      selectedId.value = res.device.id;
      ElMessage.success('PLC 设备已新增');
    }
    deviceDrawer.value = false;
    await loadDevices();
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`);
  } finally {
    deviceSaving.value = false;
  }
}

async function removeDevice(d: PlcDevice) {
  try {
    await ElMessageBox.confirm(
      `删除 PLC 设备「${d.name}」会同时删除其点位、原始读数与产量绑定关系，确定继续？`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    );
  } catch {
    return;
  }
  try {
    await adminApi.deletePlcDevice(d.id);
    ElMessage.success('已删除');
    if (selectedId.value === d.id) selectedId.value = null;
    await loadDevices();
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

/* ── 试读 ─────────────────────────────────────────────── */
const testVisible = ref(false);
const testing = ref(false);
const testResult = ref<PlcTestResult | null>(null);
const testDeviceName = ref('');

async function testDevice(d: PlcDevice) {
  testing.value = true;
  testDeviceName.value = `${d.code} ${d.name}`;
  try {
    testResult.value = await adminApi.testPlcDevice(d.id);
    testVisible.value = true;
    await loadDevices();
  } catch (e) {
    ElMessage.error(`试读失败：${(e as Error).message}`);
  } finally {
    testing.value = false;
  }
}

/* ── 点位表单 ─────────────────────────────────────────── */
const pointDrawer = ref(false);
const pointSaving = ref(false);
const editingPointId = ref<number | null>(null);

function blankPoint() {
  return {
    code: '',
    name: '',
    area: 'DB' as PlcPoint['area'],
    dbNumber: 1 as number | null,
    startOffset: 0,
    bitOffset: null as number | null,
    dataType: 'uint32' as PlcPoint['dataType'],
    role: 'bottle' as PlcPoint['role'],
    counterMode: 'cumulative' as PlcPoint['counterMode'],
    scale: 1,
    unit: '瓶',
    maxHourly: null as number | null,
    enabled: true,
    sort: 0,
    description: '',
    simBase: 1000,
    simRate: 100
  };
}
const pointForm = reactive(blankPoint());

function openPointCreate() {
  if (!selectedId.value) {
    ElMessage.warning('请先选择一台 PLC 设备');
    return;
  }
  Object.assign(pointForm, blankPoint());
  editingPointId.value = null;
  pointDrawer.value = true;
}

function openPointEdit(p: PlcPoint) {
  editingPointId.value = p.id;
  Object.assign(pointForm, {
    code: p.code,
    name: p.name,
    area: p.area,
    dbNumber: p.dbNumber,
    startOffset: p.startOffset,
    bitOffset: p.bitOffset,
    dataType: p.dataType,
    role: p.role,
    counterMode: p.counterMode,
    scale: p.scale,
    unit: p.unit,
    maxHourly: p.maxHourly,
    enabled: p.enabled,
    sort: p.sort,
    description: p.description,
    simBase: p.simBase,
    simRate: p.simRate
  });
  pointDrawer.value = true;
}

async function savePoint() {
  if (!selectedId.value) return;
  if (!pointForm.code.trim() || !pointForm.name.trim()) {
    ElMessage.warning('点位编号和名称不能为空');
    return;
  }
  pointSaving.value = true;
  try {
    const payload = {
      ...pointForm,
      code: pointForm.code.trim().toUpperCase(),
      deviceId: selectedId.value
    };
    if (editingPointId.value) {
      await adminApi.savePlcPoint(editingPointId.value, payload);
      ElMessage.success('点位已保存');
    } else {
      await adminApi.createPlcPoint(payload);
      ElMessage.success('点位已新增');
    }
    pointDrawer.value = false;
    await loadDevices();
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`);
  } finally {
    pointSaving.value = false;
  }
}

async function removePoint(p: PlcPoint) {
  try {
    await ElMessageBox.confirm(`删除点位「${p.name}」及其历史读数？`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    });
  } catch {
    return;
  }
  try {
    await adminApi.deletePlcPoint(p.id);
    ElMessage.success('已删除');
    await loadDevices();
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

onMounted(loadDevices);
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button :loading="loading" @click="loadDevices">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
        <el-button type="primary" @click="openDeviceCreate">
          <el-icon><Plus /></el-icon>&nbsp;新增 PLC 设备
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="当前没有 PLC 时请把采集模式选为「模拟数据」"
      description="模拟模式会按「模拟起始值 + 速率 × 运行小时」生成递增读数，可先跑通设备、点位、试读、产量绑定和小时产量。设备接入后改为真实 PLC（python-snap7 3.x，无需 snap7.dll），填 IP / 机架 / 槽位即可。"
    />

    <div class="stat-grid">
      <div class="stat-card"><span>PLC 设备</span><b>{{ devices.length }}</b></div>
      <div class="stat-card"><span>在线</span><b>{{ onlineCount }}</b></div>
      <div class="stat-card"><span>点位数</span><b>{{ pointTotal }}</b></div>
    </div>

    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="card-head">
          <span>PLC 设备</span>
          <span class="t-subtle">点击行查看 / 维护该设备的产量点位</span>
        </div>
      </template>
      <el-table
        v-loading="loading"
        :data="devices"
        size="small"
        border
        highlight-current-row
        @row-click="selectDevice"
      >
        <el-table-column prop="code" label="设备编号" width="120" />
        <el-table-column prop="name" label="设备名称" min-width="150" />
        <el-table-column label="地址" min-width="140">
          <template #default="{ row }"><span class="t-mono">{{ row.endpoint }}</span></template>
        </el-table-column>
        <el-table-column label="采集模式" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.mode === 'snap7' ? 'success' : 'info'">
              {{ row.mode === 'snap7' ? '真实 PLC' : '模拟' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="pointCount" label="点位" width="70" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'online' ? 'success' : 'danger'">
              {{ row.status === 'online' ? '在线' : '离线' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="lastOkAt" label="最近成功" width="160" />
        <el-table-column prop="lastError" label="最近错误" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :loading="testing" @click.stop="testDevice(row)">试读</el-button>
            <el-button link type="primary" @click.stop="openDeviceEdit(row)">编辑</el-button>
            <el-button link type="danger" @click.stop="removeDevice(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>
            产量点位
            <template v-if="selectedDevice"> · {{ selectedDevice.name }}</template>
          </span>
          <div>
            <el-button size="small" :disabled="!selectedId" @click="openPointCreate">
              <el-icon><Plus /></el-icon>&nbsp;新增点位
            </el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="!selectedId" description="请先在上方选择一台 PLC 设备" :image-size="60" />
      <el-table v-else v-loading="pointsLoading" :data="points" size="small" border>
        <el-table-column prop="code" label="点位编号" width="120" />
        <el-table-column prop="name" label="点位名称" min-width="140" />
        <el-table-column label="地址" width="110">
          <template #default="{ row }"><span class="t-mono">{{ row.address }}</span></template>
        </el-table-column>
        <el-table-column label="类型" width="130">
          <template #default="{ row }">{{ row.dataTypeLabel }}</template>
        </el-table-column>
        <el-table-column label="用途" width="150">
          <template #default="{ row }">
            <el-tag size="small" :type="row.role === 'bottle' || row.role === 'case' ? 'success' : 'info'">
              {{ row.roleLabel }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="计数方式" width="110">
          <template #default="{ row }">
            {{ row.counterMode === 'cumulative' ? '累计值' : '瞬时值' }}
          </template>
        </el-table-column>
        <el-table-column label="倍率 / 单位" width="100">
          <template #default="{ row }">{{ row.scale }} × {{ row.unit }}</template>
        </el-table-column>
        <el-table-column label="最近读数" width="130">
          <template #default="{ row }">
            <span class="t-mono">{{ row.lastValue ?? '--' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
              {{ row.enabled ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="lastError" label="最近错误" min-width="160" show-overflow-tooltip />
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openPointEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="removePoint(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 设备表单 -->
    <el-drawer v-model="deviceDrawer" :title="editingDeviceId ? '编辑 PLC 设备' : '新增 PLC 设备'" size="480px">
      <el-form :model="deviceForm" label-width="110px">
        <el-form-item label="设备编号" required>
          <el-input v-model="deviceForm.code" placeholder="如 PLC-W103" />
        </el-form-item>
        <el-form-item label="设备名称" required>
          <el-input v-model="deviceForm.name" placeholder="如 一线灌装机 PLC" />
        </el-form-item>
        <el-form-item label="采集模式">
          <el-radio-group v-model="deviceForm.mode">
            <el-radio value="simulated">模拟数据</el-radio>
            <el-radio value="snap7">真实 PLC</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="IP 地址">
          <el-input v-model="deviceForm.host" placeholder="如 192.168.2.1" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="deviceForm.port" :min="1" :max="65535" controls-position="right" />
        </el-form-item>
        <el-form-item label="机架 / 槽位">
          <el-input-number v-model="deviceForm.rack" :min="0" :max="7" controls-position="right" />
          <el-input-number v-model="deviceForm.slot" :min="0" :max="31" controls-position="right" />
          <span class="t-subtle ml-8">S7-200 SMART 通常为 0 / 1</span>
        </el-form-item>
        <el-form-item label="超时(ms)">
          <el-input-number v-model="deviceForm.timeoutMs" :min="100" :max="60000" controls-position="right" />
        </el-form-item>
        <el-form-item label="采样周期(秒)">
          <el-input-number v-model="deviceForm.intervalSeconds" :min="5" :max="86400" controls-position="right" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="deviceForm.enabled" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="deviceForm.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deviceDrawer = false">取消</el-button>
        <el-button type="primary" :loading="deviceSaving" @click="saveDevice">保存</el-button>
      </template>
    </el-drawer>

    <!-- 点位表单 -->
    <el-drawer v-model="pointDrawer" :title="editingPointId ? '编辑产量点位' : '新增产量点位'" size="480px">
      <el-form :model="pointForm" label-width="110px">
        <el-form-item label="点位编号" required>
          <el-input v-model="pointForm.code" placeholder="如 PP-W103-01" />
        </el-form-item>
        <el-form-item label="点位名称" required>
          <el-input v-model="pointForm.name" placeholder="如 一线灌装计数" />
        </el-form-item>
        <el-form-item label="存储区">
          <el-select v-model="pointForm.area" style="width: 100%">
            <el-option label="DB 数据块" value="DB" />
            <el-option label="M 位存储区" value="M" />
            <el-option label="I 输入区" value="I" />
            <el-option label="Q 输出区" value="Q" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="pointForm.area === 'DB'" label="DB 号">
          <el-input-number v-model="pointForm.dbNumber" :min="1" :max="65535" controls-position="right" />
        </el-form-item>
        <el-form-item label="起始字节">
          <el-input-number v-model="pointForm.startOffset" :min="0" :max="65535" controls-position="right" />
        </el-form-item>
        <el-form-item label="数据类型">
          <el-select v-model="pointForm.dataType" style="width: 100%">
            <el-option label="16 位无符号整数" value="uint16" />
            <el-option label="16 位有符号整数" value="int16" />
            <el-option label="32 位无符号整数" value="uint32" />
            <el-option label="32 位有符号整数" value="int32" />
            <el-option label="32 位浮点数 REAL" value="float32" />
            <el-option label="布尔位" value="bool" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="pointForm.dataType === 'bool'" label="位偏移">
          <el-input-number v-model="pointForm.bitOffset" :min="0" :max="7" controls-position="right" />
        </el-form-item>
        <el-form-item label="点位用途">
          <el-select v-model="pointForm.role" style="width: 100%">
            <el-option label="01 电眼 · 瓶数" value="bottle" />
            <el-option label="02 电眼 · 箱数" value="case" />
            <el-option label="产品规格 ml" value="spec" />
          </el-select>
        </el-form-item>
        <el-form-item label="计数方式">
          <el-radio-group v-model="pointForm.counterMode">
            <el-radio value="cumulative">累计值</el-radio>
            <el-radio value="instant">本次即区间产量</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="倍率 / 单位">
          <el-input-number v-model="pointForm.scale" :min="0" :step="0.1" controls-position="right" />
          <el-input v-model="pointForm.unit" style="width: 100px" />
        </el-form-item>
        <el-form-item label="单次增量上限">
          <el-input-number v-model="pointForm.maxHourly" :min="0" controls-position="right" />
          <span class="t-subtle ml-8">留空不限制</span>
        </el-form-item>
        <el-form-item label="模拟起始值">
          <el-input-number v-model="pointForm.simBase" controls-position="right" />
        </el-form-item>
        <el-form-item label="模拟速率/小时">
          <el-input-number v-model="pointForm.simRate" :min="0" controls-position="right" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="pointForm.enabled" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="pointForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pointDrawer = false">取消</el-button>
        <el-button type="primary" :loading="pointSaving" @click="savePoint">保存</el-button>
      </template>
    </el-drawer>

    <!-- 试读结果 -->
    <el-dialog v-model="testVisible" :title="`试读结果 · ${testDeviceName}`" width="720px">
      <el-alert
        v-if="testResult"
        :type="testResult.ok ? 'success' : 'error'"
        :closable="false"
        show-icon
        class="mb-12"
        :title="testResult.ok ? '读取成功' : '存在读取失败的点位'"
        :description="
          (testResult.simulated ? '当前为模拟数据；' : '') + `耗时 ${testResult.durationMs} ms，共 ${testResult.items.length} 个点位`
        "
      />
      <el-table :data="testResult?.items ?? []" size="small" border max-height="420">
        <el-table-column prop="point" label="点位" width="130" />
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="address" label="地址" width="110" />
        <el-table-column prop="value" label="读数" width="140" />
        <el-table-column label="结果" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.ok ? 'success' : 'danger'">{{ row.ok ? '成功' : '失败' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="180" show-overflow-tooltip />
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
.mb-12 { margin-bottom: 12px; }
.ml-8 { margin-left: 8px; }
.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.stat-card {
  background: #fff;
  border: 1px solid $yl-border;
  border-radius: 4px;
  padding: 12px 16px;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  span { font-size: 13px; color: var(--el-text-color-secondary); }
  b { font-size: 22px; color: #0c0d0e; }
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
</style>
