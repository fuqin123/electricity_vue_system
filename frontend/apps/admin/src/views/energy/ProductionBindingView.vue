/**
 * ProductionBindingView —— 能源配置管理 · 产量计量绑定（v1.8.0）
 *
 * 与「能源计量绑定」完全独立：
 *   能 源：车间 → 类型 → 设备 → 能源计量点（EnergyMeter）
 *   产 量：车间 → 类型 → 设备 → PLC 产量点位（PlcPoint）
 *
 * 左侧维护产量车间 / 类型 / 设备（可先建空壳），右侧给选中设备勾选 PLC 点位，
 * 并指定该设备对应的**真实产线**与是否计入产量：
 *   · 一条线可挂多个计数设备（如两台灌装机分别计数），勾选后合计；
 *   · 「装箱机」这类与灌装机重复计数的设备把「计入产量」关掉，只采集不计产。
 */
<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  adminApi,
  type ProductionBindingDeviceNode,
  type ProductionBindingOptions,
  type ProductionBindingPoint,
  type ProductionBindingWorkshopNode
} from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';

interface TreeNodeData {
  key: string;
  level: 'workshop' | 'type' | 'device';
  id: number;
  name: string;
  workshopId?: number;
  workshopName?: string;
  typeId?: number;
  typeName?: string;
  lineCode?: string | null;
  countsOutput?: boolean;
  pointCodes?: string[];
  children?: TreeNodeData[];
}

const loading = ref(false);
const saving = ref(false);
const workshops = ref<ProductionBindingWorkshopNode[]>([]);
const options = ref<ProductionBindingOptions>({ workshops: [], lines: [], points: [] });
const treeRef = ref<{ setCurrentKey: (key: string | number | null) => void } | null>(null);
const selectedKey = ref('');
const selectedCodes = ref<string[]>([]);
const pointKeyword = ref('');
const onlyBound = ref(false);

const treeData = computed<TreeNodeData[]>(() =>
  workshops.value.map((w) => ({
    key: `w:${w.id}`,
    level: 'workshop',
    id: w.id,
    name: w.name,
    children: w.types.map((t) => ({
      key: `t:${t.id}`,
      level: 'type',
      id: t.id,
      name: t.name,
      workshopId: w.id,
      workshopName: w.name,
      children: t.devices.map((d) => ({
        key: `d:${d.id}`,
        level: 'device',
        id: d.id,
        name: d.name,
        workshopId: w.id,
        workshopName: w.name,
        typeId: t.id,
        typeName: t.name,
        lineCode: d.lineCode,
        countsOutput: d.countsOutput,
        pointCodes: [...d.pointCodes]
      }))
    }))
  }))
);

function findNode(key: string): TreeNodeData | null {
  for (const w of treeData.value) {
    if (w.key === key) return w;
    for (const t of w.children ?? []) {
      if (t.key === key) return t;
      for (const d of t.children ?? []) {
        if (d.key === key) return d;
      }
    }
  }
  return null;
}

const selectedNode = computed(() => (selectedKey.value ? findNode(selectedKey.value) : null));
const selectedDevice = computed(() =>
  selectedNode.value?.level === 'device' ? selectedNode.value : null
);

function findDevice(id: number): ProductionBindingDeviceNode | null {
  for (const w of workshops.value) {
    for (const t of w.types) {
      for (const d of t.devices) {
        if (d.id === id) return d;
      }
    }
  }
  return null;
}

const currentDevice = computed(() => (selectedDevice.value ? findDevice(selectedDevice.value.id) : null));

watch(
  () => selectedDevice.value?.key,
  () => {
    selectedCodes.value = [...(currentDevice.value?.pointCodes ?? [])];
  },
  { immediate: true }
);

const isDirty = computed(() => {
  const d = currentDevice.value;
  if (!d) return false;
  return [...selectedCodes.value].sort().join(',') !== [...d.pointCodes].sort().join(',');
});

async function load() {
  loading.value = true;
  try {
    const res = await adminApi.getProductionBindingTree();
    workshops.value = res.workshops;
    options.value = res.options;
    if (selectedKey.value && !findNode(selectedKey.value)) selectedKey.value = '';
    await nextTick();
    if (selectedKey.value) treeRef.value?.setCurrentKey(selectedKey.value);
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    loading.value = false;
  }
}

async function onNodeClick(data: TreeNodeData) {
  if (data.key === selectedKey.value) return;
  if (isDirty.value) {
    try {
      await ElMessageBox.confirm(
        '当前设备有未保存的点位修改，切换节点后会丢失。',
        '未保存修改',
        { type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑' }
      );
    } catch {
      treeRef.value?.setCurrentKey(selectedKey.value || null);
      return;
    }
  }
  selectedKey.value = data.key;
  pointKeyword.value = '';
  onlyBound.value = false;
}

/* ── 右侧：设备与点位 ─────────────────────────────────── */

const pointPool = computed<ProductionBindingPoint[]>(() => options.value.points);

/** 某个点位还被哪些设备绑定（不含当前设备） */
const pointBindings = computed(() => {
  const map = new Map<string, string[]>();
  const currentId = currentDevice.value?.id;
  for (const w of workshops.value) {
    for (const t of w.types) {
      for (const d of t.devices) {
        if (d.id === currentId) continue;
        for (const code of d.pointCodes) {
          const label = `${w.name} / ${t.name} / ${d.name}`;
          const list = map.get(code) ?? [];
          list.push(label);
          map.set(code, list);
        }
      }
    }
  }
  return map;
});

function otherBindings(code: string): string[] {
  return pointBindings.value.get(code) ?? [];
}

const filteredPoints = computed(() => {
  const kw = pointKeyword.value.trim().toLowerCase();
  let list = pointPool.value;
  if (onlyBound.value) list = list.filter((p) => selectedCodes.value.includes(p.code));
  if (kw) {
    list = list.filter(
      (p) =>
        p.code.toLowerCase().includes(kw) ||
        p.name.toLowerCase().includes(kw) ||
        p.deviceCode.toLowerCase().includes(kw) ||
        p.deviceName.toLowerCase().includes(kw)
    );
  }
  return list;
});

function togglePoint(code: string) {
  selectedCodes.value = selectedCodes.value.includes(code)
    ? selectedCodes.value.filter((c) => c !== code)
    : [...selectedCodes.value, code];
}

function selectAllVisible() {
  const set = new Set(selectedCodes.value);
  for (const p of filteredPoints.value) set.add(p.code);
  selectedCodes.value = [...set];
}

function clearAll() {
  selectedCodes.value = [];
}

const deviceForm = reactive({
  name: '',
  lineCode: '' as string,
  countsOutput: true
});

function syncDeviceForm() {
  const d = currentDevice.value;
  if (!d) return;
  deviceForm.name = d.name;
  deviceForm.lineCode = d.lineCode ?? '';
  deviceForm.countsOutput = d.countsOutput;
}

watch(() => currentDevice.value?.id, syncDeviceForm, { immediate: true });

async function saveDeviceMeta() {
  const d = currentDevice.value;
  if (!d) return;
  if (!deviceForm.name.trim()) {
    ElMessage.warning('设备名称不能为空');
    return;
  }
  saving.value = true;
  try {
    await adminApi.updateProductionBindingDevice(d.id, {
      name: deviceForm.name.trim(),
      lineCode: deviceForm.lineCode || null,
      countsOutput: deviceForm.countsOutput
    });
    ElMessage.success('设备归属已保存');
    await load();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    saving.value = false;
  }
}

async function savePoints() {
  const d = currentDevice.value;
  if (!d) return;
  saving.value = true;
  try {
    const res = await adminApi.setProductionBindingDevicePoints(d.id, selectedCodes.value);
    ElMessage.success(`已保存「${d.name}」的 ${res.saved} 个 PLC 点位`);
    await load();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    saving.value = false;
  }
}

/* ── 左侧结构维护 ─────────────────────────────────────── */

const dialog = reactive({
  visible: false,
  level: 'workshop' as 'workshop' | 'type' | 'device',
  editing: false,
  id: null as number | null,
  parentId: null as number | null,
  parentLabel: '',
  name: '',
  lineCode: '',
  countsOutput: true
});

const dialogTitle = computed(() => {
  const name = dialog.level === 'workshop' ? '车间' : dialog.level === 'type' ? '类型' : '设备';
  return `${dialog.editing ? '修改' : '新增'}${name}`;
});

function openAdd(level: 'workshop' | 'type' | 'device', parent?: TreeNodeData) {
  Object.assign(dialog, {
    visible: true,
    level,
    editing: false,
    id: null,
    parentId: parent?.id ?? null,
    parentLabel: parent?.name ?? '',
    name: '',
    lineCode: '',
    countsOutput: true
  });
}

function openRename(node: TreeNodeData) {
  Object.assign(dialog, {
    visible: true,
    level: node.level,
    editing: true,
    id: node.id,
    parentId: null,
    parentLabel: '',
    name: node.name,
    lineCode: node.lineCode ?? '',
    countsOutput: node.countsOutput ?? true
  });
}

async function saveNode() {
  const name = dialog.name.trim();
  if (!name) {
    ElMessage.warning('名称不能为空');
    return;
  }
  saving.value = true;
  try {
    let selectKey = '';
    if (dialog.level === 'workshop') {
      if (dialog.editing && dialog.id != null) {
        await adminApi.updateProductionBindingWorkshop(dialog.id, { name });
      } else {
        const res = await adminApi.createProductionBindingWorkshop({ name });
        selectKey = `w:${res.workshop.id}`;
      }
    } else if (dialog.level === 'type') {
      if (dialog.editing && dialog.id != null) {
        await adminApi.updateProductionBindingType(dialog.id, { name });
      } else {
        if (dialog.parentId == null) throw new Error('缺少所属车间');
        const res = await adminApi.createProductionBindingType({
          workshopId: dialog.parentId,
          name
        });
        selectKey = `t:${res.type.id}`;
      }
    } else {
      if (dialog.editing && dialog.id != null) {
        await adminApi.updateProductionBindingDevice(dialog.id, {
          name,
          lineCode: dialog.lineCode || null,
          countsOutput: dialog.countsOutput
        });
      } else {
        if (dialog.parentId == null) throw new Error('缺少所属类型');
        const res = await adminApi.createProductionBindingDevice({
          typeId: dialog.parentId,
          name,
          lineCode: dialog.lineCode || null,
          countsOutput: dialog.countsOutput
        });
        selectKey = `d:${res.device.id}`;
      }
    }
    dialog.visible = false;
    ElMessage.success(dialog.editing ? '已保存' : '已新增');
    await load();
    if (selectKey) {
      selectedKey.value = selectKey;
      await nextTick();
      treeRef.value?.setCurrentKey(selectKey);
    }
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    saving.value = false;
  }
}

async function removeNode(node: TreeNodeData) {
  const levelName = node.level === 'workshop' ? '车间' : node.level === 'type' ? '类型' : '设备';
  const cascade =
    node.level === 'workshop'
      ? '其下所有类型、设备和绑定关系都会删除'
      : node.level === 'type'
        ? '其下所有设备和绑定关系都会删除'
        : '该设备的点位绑定关系会删除';
  try {
    await ElMessageBox.confirm(
      `确定删除${levelName}「${node.name}」吗？${cascade}；PLC 点位本身和原始读数不受影响。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    );
  } catch {
    return;
  }
  try {
    if (node.level === 'workshop') await adminApi.deleteProductionBindingWorkshop(node.id);
    else if (node.level === 'type') await adminApi.deleteProductionBindingType(node.id);
    else await adminApi.deleteProductionBindingDevice(node.id);
    ElMessage.success('已删除');
    if (selectedKey.value === node.key) selectedKey.value = '';
    await load();
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

function addChild() {
  const node = selectedNode.value;
  if (!node) {
    openAdd('workshop');
    return;
  }
  if (node.level === 'workshop') openAdd('type', node);
  else if (node.level === 'type') openAdd('device', node);
  else ElMessage.info('设备下不能再加层级，请在右侧勾选 PLC 点位');
}

onMounted(load);
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
        <el-button type="primary" @click="openAdd('workshop')">
          <el-icon><Plus /></el-icon>&nbsp;新增车间
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="产量计量绑定只决定「哪个 PLC 点位算到哪条产线」，不改 PLC 配置与原始读数"
      description="左侧：车间 → 产量类型 → 设备；右侧：勾选 PLC 产量点位。设备必须指定真实产线才会入库；装箱机等重复计数设备请关闭「计入产量」。"
    />

    <div class="binding-layout">
      <!-- 左树 -->
      <el-card shadow="never" class="tree-card" v-loading="loading">
        <template #header>
          <div class="card-head">
            <span>产量绑定结构</span>
            <div>
              <el-button link type="primary" @click="addChild">
                {{ selectedNode ? '新增下级' : '新增车间' }}
              </el-button>
            </div>
          </div>
        </template>
        <el-tree
          ref="treeRef"
          :data="treeData"
          node-key="key"
          :props="{ label: 'name', children: 'children' }"
          highlight-current
          default-expand-all
          @node-click="onNodeClick"
        >
          <template #default="{ data }">
            <div class="tree-node">
              <span class="tree-node__label">
                <el-tag v-if="data.level === 'workshop'" size="small" type="info">车间</el-tag>
                <el-tag v-else-if="data.level === 'type'" size="small" type="warning">类型</el-tag>
                <el-tag v-else size="small" type="success">设备</el-tag>
                <span>{{ data.name }}</span>
                <span v-if="data.level === 'device' && data.lineCode" class="t-subtle">
                  · {{ data.lineCode }}
                </span>
                <span v-if="data.level === 'device' && data.countsOutput === false" class="t-subtle">
                  · 不计产
                </span>
              </span>
              <span class="tree-node__ops" @click.stop>
                <el-button
                  v-if="data.level !== 'device'"
                  link
                  type="primary"
                  @click="openAdd(data.level === 'workshop' ? 'type' : 'device', data)"
                >
                  加下级
                </el-button>
                <el-button link type="primary" @click="openRename(data)">改名</el-button>
                <el-button link type="danger" @click="removeNode(data)">删除</el-button>
              </span>
            </div>
          </template>
        </el-tree>
      </el-card>

      <!-- 右侧 -->
      <el-card shadow="never" class="detail-card">
        <template #header>
          <div class="card-head">
            <span>
              {{ selectedDevice ? `设备：${selectedDevice.name}` : '请选择左侧设备' }}
            </span>
            <el-button
              v-if="selectedDevice"
              type="primary"
              :loading="saving"
              @click="savePoints"
            >
              保存点位勾选
            </el-button>
          </div>
        </template>

        <el-empty v-if="!selectedDevice" description="选择左侧「设备」节点后，在这里指定产线并勾选 PLC 点位" :image-size="72" />

        <template v-else>
          <el-form :model="deviceForm" label-width="110px" class="device-form">
            <el-form-item label="设备名称">
              <el-input v-model="deviceForm.name" style="width: 240px" />
            </el-form-item>
            <el-form-item label="对应产线">
              <el-select v-model="deviceForm.lineCode" clearable placeholder="不选 = 只采集不入库" style="width: 240px">
                <el-option
                  v-for="l in options.lines"
                  :key="l.code"
                  :label="`${l.name}（${l.code}）`"
                  :value="l.code"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="计入产量">
              <el-switch v-model="deviceForm.countsOutput" />
              <span class="t-subtle ml-8">重复计数的装箱机 / 包装机请关闭</span>
            </el-form-item>
            <el-form-item>
              <el-button :loading="saving" @click="saveDeviceMeta">保存设备归属</el-button>
            </el-form-item>
          </el-form>

          <div class="point-toolbar">
            <el-input
              v-model="pointKeyword"
              size="small"
              clearable
              placeholder="搜索点位 / PLC 设备"
              style="width: 240px"
            />
            <el-checkbox v-model="onlyBound" size="small">只看已勾选</el-checkbox>
            <el-button size="small" @click="selectAllVisible">全选当前</el-button>
            <el-button size="small" @click="clearAll">清空</el-button>
            <span class="t-subtle">已勾选 {{ selectedCodes.length }} 个</span>
          </div>

          <el-table :data="filteredPoints" size="small" border max-height="440">
            <el-table-column width="48">
              <template #default="{ row }">
                <el-checkbox
                  :model-value="selectedCodes.includes(row.code)"
                  @change="togglePoint(row.code)"
                />
              </template>
            </el-table-column>
            <el-table-column prop="code" label="点位编号" width="130" />
            <el-table-column prop="name" label="点位名称" min-width="150" />
            <el-table-column label="PLC 设备" min-width="140">
              <template #default="{ row }">{{ row.deviceName }}（{{ row.deviceCode }}）</template>
            </el-table-column>
            <el-table-column prop="address" label="地址" width="110" />
            <el-table-column label="计数方式" width="100">
              <template #default="{ row }">
                {{ row.counterMode === 'cumulative' ? '累计值' : '瞬时值' }}
              </template>
            </el-table-column>
            <el-table-column prop="unit" label="单位" width="70" />
            <el-table-column label="同时绑定于" min-width="180">
              <template #default="{ row }">
                <span v-if="otherBindings(row.code).length" class="t-subtle">
                  {{ otherBindings(row.code).join('、') }}
                </span>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="70">
              <template #default="{ row }">
                <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
                  {{ row.enabled ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </el-card>
    </div>

    <!-- 结构维护对话框 -->
    <el-dialog v-model="dialog.visible" :title="dialogTitle" width="460px">
      <el-form :model="dialog" label-width="90px">
        <el-form-item v-if="dialog.parentId" label="所属">
          <span class="t-subtle">{{ dialog.parentLabel }}</span>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="dialog.name" placeholder="输入名称" />
        </el-form-item>
        <template v-if="dialog.level === 'device'">
          <el-form-item label="对应产线">
            <el-select v-model="dialog.lineCode" clearable placeholder="不选 = 只采集不入库" style="width: 100%">
              <el-option
                v-for="l in options.lines"
                :key="l.code"
                :label="`${l.name}（${l.code}）`"
                :value="l.code"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="计入产量">
            <el-switch v-model="dialog.countsOutput" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveNode">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
.ml-8 { margin-left: 8px; }
.binding-layout {
  display: grid;
  grid-template-columns: 380px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.tree-card :deep(.el-card__body) {
  max-height: 680px;
  overflow: auto;
  padding: 8px;
}
.tree-node {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  font-size: 13px;
  padding-right: 6px;
}
.tree-node__label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
}
.tree-node__ops {
  display: none;
  flex-shrink: 0;
}
.tree-node:hover .tree-node__ops {
  display: inline-flex;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.device-form {
  :deep(.el-form-item) { margin-bottom: 10px; }
}
.point-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: 4px 0 10px;
}
</style>
