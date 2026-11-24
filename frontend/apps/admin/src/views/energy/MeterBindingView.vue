<script setup lang="ts">
/**
 * MeterBindingView —— 能源配置管理 · 计量绑定（v1.7.4）
 *
 * 左树：车间 → 类型 → 设备，全部可自定义新增 / 改名 / 删除，且允许先建空壳。
 * 右侧：现有计量点池，按选中设备勾选要参与合计的表。
 *
 * ⚠ 绑定只决定**查询口径**，不复制、不修改任何原始读数；删除节点也不会删
 *   计量点或历史数据。同一块表允许出现在多个设备中（共享辅机表现场存在），
 *   右侧会标注「同时绑定于」哪些设备，避免无意识地重复计量。
 * ⚠ 计量页在该能源只要存在「设备 + 启用表」，就完全按这棵树展示；
 *   没有配置的能源仍用原始计量台账的固定分类兜底。
 */
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  adminApi,
  type MeterBindingEnergy,
  type MeterBindingMeter,
  type MeterBindingOptions,
  type MeterBindingWorkshopNode
} from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';

interface TreeNodeData {
  key: string;
  level: 'workshop' | 'type' | 'device';
  id: number;
  name: string;
  energy?: MeterBindingEnergy;
  energyName?: string;
  unit?: string;
  workshopId?: number;
  workshopName?: string;
  typeId?: number;
  typeName?: string;
  meterCodes?: string[];
  meters?: MeterBindingMeter[];
  meterCount?: number;
  children?: TreeNodeData[];
}

const loading = ref(false);
const saving = ref(false);
const workshops = ref<MeterBindingWorkshopNode[]>([]);
const options = ref<MeterBindingOptions>({
  workshops: [],
  energies: [],
  usageTypes: [],
  deviceNames: [],
  meters: []
});
const treeRef = ref<{ setCurrentKey: (key: string | number | null) => void } | null>(null);
const selectedKey = ref('');
const selectedCodes = ref<string[]>([]);
const meterKeyword = ref('');
const onlyBound = ref(false);

const treeData = computed<TreeNodeData[]>(() =>
  workshops.value.map((w) => ({
    key: `w:${w.id}`,
    level: 'workshop',
    id: w.id,
    name: w.name,
    meterCount: w.meterCount,
    children: w.types.map((t) => ({
      key: `t:${t.id}`,
      level: 'type',
      id: t.id,
      name: t.name,
      energy: t.energy,
      energyName: t.energyName,
      unit: t.unit,
      workshopId: w.id,
      workshopName: w.name,
      meterCount: t.meterCount,
      children: t.devices.map((d) => ({
        key: `d:${d.id}`,
        level: 'device',
        id: d.id,
        name: d.name,
        energy: d.energy,
        energyName: d.energyName,
        unit: d.unit,
        workshopId: w.id,
        workshopName: w.name,
        typeId: t.id,
        typeName: t.name,
        meterCodes: [...d.meterCodes],
        meters: [...d.meters],
        meterCount: d.meterCount
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

watch(
  () => selectedDevice.value?.meterCodes?.join(','),
  () => {
    selectedCodes.value = [...(selectedDevice.value?.meterCodes ?? [])];
  },
  { immediate: true }
);

const isDirty = computed(() => {
  const d = selectedDevice.value;
  if (!d) return false;
  const a = [...selectedCodes.value].sort().join(',');
  const b = [...(d.meterCodes ?? [])].sort().join(',');
  return a !== b;
});

async function load() {
  loading.value = true;
  try {
    const res = await adminApi.getBindingTree();
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
        '当前设备有未保存的绑定修改，切换节点后会丢失。',
        '未保存修改',
        { type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑' }
      );
    } catch {
      // 用户取消：把树的高亮恢复到原来选中的节点
      treeRef.value?.setCurrentKey(selectedKey.value || null);
      return;
    }
  }
  selectedKey.value = data.key;
  meterKeyword.value = '';
  onlyBound.value = false;
}

/* ── 右侧：计量点分配 ─────────────────────────────────── */

const energyMeters = computed(() =>
  selectedDevice.value
    ? options.value.meters.filter((m) => m.energy === selectedDevice.value?.energy)
    : []
);

/** 某块表还被哪些设备绑定（不含当前设备） */
const assignmentsByMeter = computed(() => {
  const map = new Map<string, string[]>();
  const currentId = selectedDevice.value?.id;
  for (const w of workshops.value) {
    for (const t of w.types) {
      for (const d of t.devices) {
        if (d.id === currentId) continue;
        for (const m of d.meters) {
          const label = `${w.name} / ${t.name} / ${d.name}`;
          const list = map.get(m.code) ?? [];
          list.push(label);
          map.set(m.code, list);
        }
      }
    }
  }
  return map;
});

function otherBindings(code: string): string[] {
  return assignmentsByMeter.value.get(code) ?? [];
}

const filteredMeters = computed(() => {
  const kw = meterKeyword.value.trim().toLowerCase();
  let list = energyMeters.value;
  if (onlyBound.value) list = list.filter((m) => selectedCodes.value.includes(m.code));
  if (kw) {
    list = list.filter(
      (m) =>
        m.code.toLowerCase().includes(kw) ||
        m.name.toLowerCase().includes(kw) ||
        (m.workshopName ?? '').toLowerCase().includes(kw)
    );
  }
  return list;
});

function toggleMeter(code: string) {
  selectedCodes.value = selectedCodes.value.includes(code)
    ? selectedCodes.value.filter((c) => c !== code)
    : [...selectedCodes.value, code];
}

function selectAllVisible() {
  const set = new Set(selectedCodes.value);
  for (const m of filteredMeters.value) set.add(m.code);
  selectedCodes.value = [...set];
}

function clearAll() {
  selectedCodes.value = [];
}

async function saveMeters() {
  if (!selectedDevice.value) return;
  saving.value = true;
  try {
    const res = await adminApi.setBindingDeviceMeters(selectedDevice.value.id, selectedCodes.value);
    ElMessage.success(`已保存「${selectedDevice.value.name}」的 ${res.saved} 块表`);
    await load();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    saving.value = false;
  }
}

/* ── 左侧：树节点增删改 ───────────────────────────────── */

const dialog = reactive({
  visible: false,
  level: 'workshop' as TreeNodeData['level'],
  editing: false,
  id: null as number | null,
  parentId: null as number | null,
  parentLabel: '',
  name: '',
  energy: 'elec' as MeterBindingEnergy
});

const dialogTitle = computed(() => {
  const levelName = dialog.level === 'workshop' ? '车间' : dialog.level === 'type' ? '类型' : '设备';
  return `${dialog.editing ? '修改' : '新增'}${levelName}`;
});

const workshopSuggestions = computed(() =>
  [...new Set([
    ...options.value.workshops.map((w) => w.name),
    ...workshops.value.map((w) => w.name)
  ])].sort()
);

const typeSuggestions = computed(() => {
  const set = new Set<string>();
  for (const m of options.value.meters) {
    if (m.energy === dialog.energy && m.usageType) set.add(m.usageType);
  }
  for (const w of workshops.value) {
    for (const t of w.types) {
      if (t.energy === dialog.energy) set.add(t.name);
    }
  }
  return [...set].sort();
});

const deviceSuggestions = computed(() => {
  const set = new Set<string>();
  for (const m of options.value.meters) {
    if (m.energy === dialog.energy && m.deviceName) set.add(m.deviceName);
  }
  for (const w of workshops.value) {
    for (const t of w.types) {
      if (dialog.parentId != null && t.id !== dialog.parentId) continue;
      for (const d of t.devices) set.add(d.name);
    }
  }
  return [...set].sort();
});

/**
 * 改名/新增名称建议。
 *
 * 用 `el-autocomplete` 而不是 `allow-create` 的 select：后者要按回车才把
 * 手输值写进 v-model，用户输完直接点“保存”时可能仍是旧名字 ——
 * 表现就是「改名没反应」。autocomplete 的 v-model 随输入即时更新。
 */
function fetchNameSuggestions(
  query: string,
  callback: (items: Array<{ value: string }>) => void
) {
  const source =
    dialog.level === 'workshop'
      ? workshopSuggestions.value
      : dialog.level === 'type'
        ? typeSuggestions.value
        : deviceSuggestions.value;
  const kw = query.trim().toLowerCase();
  callback(
    source
      .filter((name) => !kw || name.toLowerCase().includes(kw))
      .map((name) => ({ value: name }))
  );
}

/** 结构变更会重新拉树；若右侧有未保存的勾选，先确认是否放弃。 */
async function confirmDiscardDirty(): Promise<boolean> {
  if (!isDirty.value) return true;
  try {
    await ElMessageBox.confirm(
      '右侧有未保存的计量点勾选，继续操作会丢失这些修改。',
      '未保存修改',
      { type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑' }
    );
    return true;
  } catch {
    return false;
  }
}

async function openAddWorkshop() {
  if (!(await confirmDiscardDirty())) return;
  Object.assign(dialog, {
    visible: true,
    level: 'workshop',
    editing: false,
    id: null,
    parentId: null,
    parentLabel: '',
    name: '',
    energy: 'elec'
  });
}

async function openAddChild(data: TreeNodeData) {
  if (!(await confirmDiscardDirty())) return;
  if (data.level === 'workshop') {
    Object.assign(dialog, {
      visible: true,
      level: 'type',
      editing: false,
      id: null,
      parentId: data.id,
      parentLabel: data.name,
      name: '',
      energy: 'elec' as MeterBindingEnergy
    });
  } else if (data.level === 'type') {
    Object.assign(dialog, {
      visible: true,
      level: 'device',
      editing: false,
      id: null,
      parentId: data.id,
      parentLabel: `${data.workshopName ?? ''} / ${data.name}`,
      name: '',
      energy: data.energy ?? 'elec'
    });
  }
}

async function openRename(data: TreeNodeData) {
  if (!(await confirmDiscardDirty())) return;
  Object.assign(dialog, {
    visible: true,
    level: data.level,
    editing: true,
    id: data.id,
    parentId: data.level === 'type' ? data.workshopId ?? null : data.typeId ?? null,
    parentLabel:
      data.level === 'workshop'
        ? ''
        : data.level === 'type'
          ? data.workshopName ?? ''
          : `${data.workshopName ?? ''} / ${data.typeName ?? ''}`,
    name: data.name,
    energy: data.energy ?? 'elec'
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
        await adminApi.updateBindingWorkshop(dialog.id, { name });
      } else {
        const res = await adminApi.createBindingWorkshop({ name });
        selectKey = `w:${res.workshop.id}`;
      }
    } else if (dialog.level === 'type') {
      if (dialog.editing && dialog.id != null) {
        await adminApi.updateBindingType(dialog.id, { name });
      } else {
        if (dialog.parentId == null) throw new Error('缺少所属车间');
        const res = await adminApi.createBindingType({
          workshopId: dialog.parentId,
          energy: dialog.energy,
          name
        });
        selectKey = `t:${res.type.id}`;
      }
    } else {
      if (dialog.editing && dialog.id != null) {
        await adminApi.updateBindingDevice(dialog.id, { name });
      } else {
        if (dialog.parentId == null) throw new Error('缺少所属类型');
        const res = await adminApi.createBindingDevice({ typeId: dialog.parentId, name });
        selectKey = `d:${res.device.id}`;
      }
    }
    dialog.visible = false;
    ElMessage.success(dialog.editing ? '已保存' : '已新增');
    if (isDirty.value) selectedKey.value = '';
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

async function removeNode(data: TreeNodeData) {
  if (!(await confirmDiscardDirty())) return;
  const levelName = data.level === 'workshop' ? '车间' : data.level === 'type' ? '类型' : '设备';
  const cascade =
    data.level === 'workshop'
      ? '其下所有类型、设备和绑定关系都会删除'
      : data.level === 'type'
        ? '其下所有设备和绑定关系都会删除'
        : '该设备的绑定关系会删除';
  try {
    await ElMessageBox.confirm(
      `确定删除${levelName}「${data.name}」吗？${cascade}；计量点本身和原始读数不受影响。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    );
  } catch {
    return;
  }
  try {
    if (data.level === 'workshop') await adminApi.deleteBindingWorkshop(data.id);
    else if (data.level === 'type') await adminApi.deleteBindingType(data.id);
    else await adminApi.deleteBindingDevice(data.id);
    ElMessage.success('已删除');
    if (isDirty.value) selectedKey.value = '';
    await load();
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

function renameSelectedDevice() {
  if (selectedNode.value) void openRename(selectedNode.value);
}

function removeSelectedDevice() {
  if (selectedNode.value) void removeNode(selectedNode.value);
}

onMounted(load);
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button type="primary" @click="openAddWorkshop">新增车间</el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="左侧维护查询结构，右侧给选中设备分配计量点"
      description="车间 / 类型 / 设备都可以自定义新增、改名、删除，也可以先建空节点再绑定表。绑定只决定查询口径，不复制、不修改原始读数；同一块表允许出现在多个设备中，右侧会提示「同时绑定于」。"
    />

    <div class="binding-layout">
      <el-card shadow="never" class="binding-left">
        <template #header>
          <div class="card-head">
            <span>绑定结构</span>
            <span class="t-subtle">{{ workshops.length }} 个车间</span>
          </div>
        </template>

        <el-empty
          v-if="!loading && !treeData.length"
          description="还没有绑定车间，点击右上角「新增车间」开始"
          :image-size="80"
        />

        <el-tree
          v-show="treeData.length"
          ref="treeRef"
          v-loading="loading"
          :data="treeData"
          node-key="key"
          :props="{ label: 'name', children: 'children' }"
          :expand-on-click-node="false"
          :default-expand-all="true"
          :current-node-key="selectedKey"
          highlight-current
          class="binding-tree"
          @node-click="onNodeClick"
        >
          <template #default="{ data }">
            <div class="tree-node">
              <span class="tree-node__label">
                <el-tag
                  size="small"
                  effect="plain"
                  :type="data.level === 'workshop' ? '' : data.level === 'type' ? 'success' : 'warning'"
                >
                  {{ data.level === 'workshop' ? '车间' : data.level === 'type' ? '类型' : '设备' }}
                </el-tag>
                <span class="tree-node__name">{{ data.name }}</span>
                <el-tag v-if="data.energyName" size="small" type="info">
                  {{ data.energyName }}
                </el-tag>
                <span v-if="data.meterCount" class="tree-node__count">{{ data.meterCount }} 表</span>
              </span>
              <span class="tree-node__actions" @click.stop>
                <el-button
                  v-if="data.level !== 'device'"
                  link
                  type="primary"
                  size="small"
                  @click="openAddChild(data)"
                >
                  ＋
                </el-button>
                <el-button link type="primary" size="small" @click="openRename(data)">
                  改名
                </el-button>
                <el-button link type="danger" size="small" @click="removeNode(data)">
                  删除
                </el-button>
              </span>
            </div>
          </template>
        </el-tree>
      </el-card>

      <el-card shadow="never" class="binding-right">
        <template #header>
          <div class="card-head">
            <span v-if="selectedDevice">
              {{ selectedDevice.workshopName }} / {{ selectedDevice.typeName }} /
              <strong>{{ selectedDevice.name }}</strong>
              <el-tag size="small" type="info" class="ml-6">{{ selectedDevice.energyName }}</el-tag>
            </span>
            <span v-else>计量点分配</span>
            <span v-if="selectedDevice" class="head-actions">
              <span class="t-subtle">已选 {{ selectedCodes.length }} 块</span>
              <el-tag v-if="isDirty" size="small" type="warning">未保存</el-tag>
              <el-button link type="primary" size="small" @click="renameSelectedDevice">
                重命名设备
              </el-button>
              <el-button link type="danger" size="small" @click="removeSelectedDevice">
                删除设备
              </el-button>
            </span>
          </div>
        </template>

        <el-empty
          v-if="!selectedDevice"
          description="在左侧选中一个「设备」，右侧会列出该能源下的全部计量点"
          :image-size="90"
        />

        <template v-else>
          <div class="meter-toolbar">
            <el-input
              v-model="meterKeyword"
              clearable
              placeholder="搜索点位编号 / 名称 / 实际车间"
              style="width: 260px"
            />
            <el-switch v-model="onlyBound" active-text="只看已绑定" />
            <el-button @click="selectAllVisible">全选当前</el-button>
            <el-button @click="clearAll">清空</el-button>
            <el-button
              type="primary"
              :loading="saving"
              :disabled="!isDirty"
              @click="saveMeters"
            >
              保存绑定
            </el-button>
          </div>

          <el-table :data="filteredMeters" size="small" border max-height="520" row-key="code">
            <el-table-column width="48" align="center">
              <template #default="{ row }">
                <el-checkbox
                  :model-value="selectedCodes.includes(row.code)"
                  @change="toggleMeter(row.code)"
                />
              </template>
            </el-table-column>
            <el-table-column prop="code" label="点位编号" width="140" />
            <el-table-column prop="name" label="点位名称" min-width="180" />
            <el-table-column prop="workshopName" label="实际车间" width="110" />
            <el-table-column prop="usageType" label="台账类型" width="120" />
            <el-table-column prop="deviceName" label="台账设备名" width="120" />
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
                  {{ row.enabled ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="同时绑定于" min-width="200">
              <template #default="{ row }">
                <template v-if="otherBindings(row.code).length">
                  <el-tag
                    v-for="label in otherBindings(row.code)"
                    :key="label"
                    size="small"
                    type="warning"
                    class="mr-4"
                  >
                    {{ label }}
                  </el-tag>
                </template>
                <span v-else class="t-subtle">—</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="field-hint">
            右侧只列当前设备能源类型下的计量点；停用的表可以保留在绑定里，但查询时会跳过。
            保存后计量页即可按该设备查询。
          </div>
        </template>
      </el-card>
    </div>

    <el-dialog v-model="dialog.visible" :title="dialogTitle" width="500px" destroy-on-close>
      <el-form label-width="86px" size="small">
        <el-form-item v-if="dialog.parentLabel" label="所属">
          <span>{{ dialog.parentLabel }}</span>
        </el-form-item>
        <el-form-item v-if="dialog.level === 'type'" label="能源类型">
          <el-select v-model="dialog.energy" :disabled="dialog.editing" style="width: 100%">
            <el-option
              v-for="e in options.energies"
              :key="e.code"
              :label="`${e.name}（${e.unit}）`"
              :value="e.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="dialogTitle.replace(/^(新增|修改)/, '') + '名称'">
          <el-autocomplete
            v-model="dialog.name"
            :fetch-suggestions="fetchNameSuggestions"
            :placeholder="`输入或选择${dialogTitle.replace(/^(新增|修改)/, '')}名称`"
            clearable
            style="width: 100%"
          />
        </el-form-item>
        <div class="field-hint">
          名称可自由输入；同一车间下类型不重名，同一类型下设备不重名。
        </div>
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
.ml-6 { margin-left: 6px; }
.mr-4 { margin-right: 4px; }
.binding-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.binding-left {
  flex: 0 0 380px;
  width: 380px;
}
.binding-right {
  flex: 1;
  min-width: 0;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.binding-tree {
  min-height: 460px;
  --el-tree-node-hover-bg-color: var(--el-fill-color-light);
}
.tree-node {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding-right: 4px;
}
.tree-node__label {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.tree-node__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree-node__count {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}
.tree-node__actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  opacity: 0.6;
}
.tree-node:hover .tree-node__actions {
  opacity: 1;
}
.tree-node__actions :deep(.el-button) {
  padding: 0 3px;
  font-size: 12px;
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.meter-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.field-hint {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-placeholder);
}
</style>
