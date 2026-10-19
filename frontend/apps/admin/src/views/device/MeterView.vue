<script setup lang="ts">
/**
 * MeterView —— 计量点位（网关、IP、端口、从站号、寄存器、倍率的配置入口）
 *
 * 这一页是**现场新增/调整仪表的唯一入口**，不需要登服务器改配置文件：
 *   · 新增点位 → 填网关、从站号、寄存器起始地址、寄存器数量、倍率、单位；
 *   · 改完点「试采」→ 立刻返回真实读数与原始报文，当场确认参数对不对；
 *   · 试采通过后无需其它操作，下一轮采集（默认 60s）就会带上它。
 *
 * ⚠ 两个必须知道的约定（否则现场容易踩）：
 *   ① 串口服务器是 **Modbus RTU over TCP**，不是 Modbus TCP —— 只填「从站号 + 寄存器」，
 *      没有 Modbus TCP 的「单元标识符」概念（组帧方式不同）；
 *   ② 后台改过的记录会标记 `source = 后台维护`，此后 `seed_registry` 默认**不再覆盖**它。
 *      想以配置文件为准必须显式加 `--force`（见 backend/docs/采集说明.md）。
 */
import { computed, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { adminApi, METER_DATA_TYPE_OPTIONS, REGISTERS_BY_DATA_TYPE, type Gateway, type LineMaster, type MeterDataType, type MeterPoint } from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';
import TablePage from '@/components/TablePage.vue';
import type { TableColumn } from '@/components/table-types';
import { useSourceBanner } from '@/utils/source';

/* ⚠ 不能用 InstanceType<typeof TablePage>：TablePage 是泛型 SFC，
   推导出的类型不是构造签名，vue-tsc 会报「does not satisfy the constraint」。
   这里按需要用到的方法声明结构类型即可。 */
const tableRef = ref<{ reload: (resetPage?: boolean) => void } | null>(null);
const banner = useSourceBanner('admin.meters');

/* ── 筛选 ─────────────────────────────────────────────── */
const kind = ref('');
const gatewayCode = ref('');
const status = ref('');
const gateways = ref<Gateway[]>([]);

const extraQuery = computed(() => ({
  ...(kind.value ? { kind: kind.value } : {}),
  ...(gatewayCode.value ? { gateway: gatewayCode.value } : {}),
  ...(status.value ? { status: status.value } : {})
}));

function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return '--';
  return v.toLocaleString('zh-CN', { maximumFractionDigits: 3 });
}

/* ── 列定义 ───────────────────────────────────────────── */
const columns: TableColumn<MeterPoint>[] = [
  { prop: 'code', label: '点位编号', width: 116 },
  { prop: 'name', label: '点位名称', minWidth: 190 },
  { prop: 'category', label: '类别', width: 70 },
  { prop: 'lineCode', label: '所属产线', width: 92 },
  { prop: 'gatewayCode', label: '网关', width: 116 },
  { prop: 'address', label: '地址 / 从站 / 寄存器', minWidth: 260 },
  // v1.5.3：数据类型直接列出来 —— 读数离谱时第一件事就是核对这一格
  { prop: 'dataType', label: '数据类型', width: 96 },
  { prop: 'factor', label: '倍率', width: 74, mono: true },
  { prop: 'unit', label: '单位', width: 66 },
  { prop: 'lastValue', label: '最近累计值', width: 130, mono: true, formatter: (r) => fmtNum(r.lastValue) },
  { prop: 'todayUsage', label: '今日用量', width: 104, mono: true, formatter: (r) => fmtNum(r.todayUsage) },
  { prop: 'status', label: '状态', width: 86, slot: 'col-status' },
  { prop: 'lastAt', label: '最后上报', width: 160, mono: true }
];

const STATUS_MAP: Record<string, { text: string; type: 'success' | 'danger' | 'warning' | 'info' }> = {
  online: { text: '在线', type: 'success' },
  offline: { text: '离线', type: 'info' },
  fault: { text: '故障', type: 'warning' }
};

/* ── 表单（抽屉）───────────────────────────────────────── */
const drawer = ref(false);
const saving = ref(false);
const editingId = ref<number | null>(null);

function blankForm() {
  return {
    code: '',
    name: '',
    kind: 'elec' as MeterPoint['kind'],
    scope: 'line' as MeterPoint['scope'],
    role: 'line' as MeterPoint['role'],
    // 归属节点由 openCreate() 按实际节点列表回填，不写死 'A1'（B-541）
    lineCode: '',
    gatewayId: null as number | null,
    slave: 1 as number | null,
    register: 1000 as number | null,
    // 默认 = 现场既有口径（32 位无符号整数、高字在前、寄存器内大端）＝ 13 块电表的读法
    dataType: 'uint32' as MeterDataType,
    byteOrder: 'big' as MeterPoint['byteOrder'],
    funcCode: 3,
    wordOrder: 'big' as MeterPoint['wordOrder'],
    urlPath: '',
    paramsText: '{}',
    valuePath: '',
    timePath: '',
    scale: 0.1,
    unit: 'kWh',
    maxHourly: null as number | null,
    enabled: true,
    remark: '',
    assetNo: '',
    installDate: ''
  };
}

const form = reactive(blankForm());

/* ── 组织节点（所属产线 / 辅助计量单元）─────────────────────
 * ⚠ v1.5.2 修复（B-541）：这里原来把选项**写死成 ['A1'..'A6']**，
 *   于是现场新增辅机 / 调配 / 瓶胚的点位时根本选不到归属节点 ——
 *   而那三类电表恰恰不挂在产线上（挂 AUX-CARB-AIR / -BLEND / -PREFORM）。
 *   改为从 `admin/lines` 拉全部组织节点，按 nodeType 分两组展示。
 */
const nodes = ref<LineMaster[]>([]);

/** 可被计量点归属的节点：产线 + 辅助计量单元（车间走 scope=车间总表，不在此列） */
const nodeGroups = computed(() => {
  const lines: LineMaster[] = [];
  const auxes: LineMaster[] = [];
  for (const n of nodes.value) {
    // 演示数据没有 nodeType，按产线处理（降级不报错）
    const t = n.nodeType ?? 'line';
    if (t === 'line') lines.push(n);
    else if (t === 'aux') auxes.push(n);
  }
  const groups: { label: string; items: LineMaster[] }[] = [];
  if (lines.length) groups.push({ label: '产线', items: lines });
  if (auxes.length) groups.push({ label: '辅助计量单元（辅机 / 调配 / 瓶胚）', items: auxes });
  return groups;
});

/** 新增时的默认归属：取第一个产线节点，而不是写死 'A1' */
function defaultNodeCode(): string {
  return nodeGroups.value[0]?.items[0]?.code ?? '';
}

/** 当前选择的网关 —— 决定表单显示 Modbus 段还是 HTTP 段 */
const currentGateway = computed(() => gateways.value.find((g) => g.id === form.gatewayId) ?? null);
const isModbus = computed(() => currentGateway.value?.kind !== 'http_api');

/**
 * 类别与单位联动：换类别时给一个合理默认值，避免忘填单位。
 *
 * ⚠ 水的单位是 **m³**（不是 t）—— 与后端 `EnergyType.DEFAULT_UNIT` 保持一致。
 *   市政水表的读数就是体积；写成 t 等于给它安了一个它没有的物理量
 *   （质量要另经密度换算，本项目没有这一步）。
 */
const UNIT_BY_KIND: Record<MeterPoint['kind'], string> = { elec: 'kWh', water: 'm³', steam: 't' };
const CATEGORY_BY_KIND: Record<MeterPoint['kind'], string> = { elec: '电', water: '水', steam: '汽' };

/** 数据类型 → 寄存器个数（只读展示；真值在后端，见 MeterPoint.registers 的说明） */
function registersOf(t: MeterDataType): number {
  return REGISTERS_BY_DATA_TYPE[t] ?? 2;
}

function openCreate() {
  Object.assign(form, blankForm());
  editingId.value = null;
  if (gateways.value.length) form.gatewayId = gateways.value[0].id;
  // 归属默认取真实节点列表的第一个产线（而不是写死的 'A1'）
  form.lineCode = defaultNodeCode();
  drawer.value = true;
}

function openEdit(row: MeterPoint) {
  editingId.value = row.id;
  Object.assign(form, {
    code: row.code,
    name: row.name,
    kind: row.kind,
    scope: row.scope,
    role: row.role,
    lineCode: row.lineCode === '—' ? '' : row.lineCode,
    gatewayId: row.gatewayId,
    slave: row.slave,
    register: row.register,
    // 老数据可能没有 dataType（接口未升级时），兜到既有口径，避免表单显示空白
    dataType: (row.dataType ?? 'uint32') as MeterDataType,
    byteOrder: row.byteOrder ?? 'big',
    funcCode: row.funcCode,
    wordOrder: row.wordOrder,
    urlPath: row.urlPath,
    paramsText: JSON.stringify(row.params ?? {}),
    valuePath: row.valuePath,
    timePath: row.timePath,
    scale: row.factor,
    unit: row.unit,
    maxHourly: row.maxHourly,
    enabled: row.enabled,
    remark: row.remark,
    assetNo: row.assetNo,
    installDate: row.installDate
  });
  drawer.value = true;
}

async function submit() {
  if (!form.code.trim() || !form.name.trim()) {
    ElMessage.warning('点位编号与名称为必填项');
    return;
  }
  if (!form.gatewayId) {
    ElMessage.warning('请选择所属网关');
    return;
  }
  // 产线级点位必须有归属节点：产线或辅助计量单元（辅机 / 调配 / 瓶胚）
  if (form.scope === 'line' && !form.lineCode) {
    ElMessage.warning('请选择「所属节点」（产线或辅助计量单元）');
    return;
  }
  let params: Record<string, unknown> = {};
  try {
    params = form.paramsText.trim() ? JSON.parse(form.paramsText) : {};
  } catch {
    ElMessage.warning('接口参数不是合法 JSON');
    return;
  }
  if (isModbus.value && (form.slave === null || form.register === null)) {
    ElMessage.warning('串口服务器类型的点位必须填「从站号」与「寄存器起始地址」');
    return;
  }
  if (!isModbus.value && !form.urlPath.trim()) {
    ElMessage.warning('HTTP API 类型的点位必须填「接口路径」');
    return;
  }

  saving.value = true;
  try {
    await adminApi.saveMeter(
      {
        code: form.code.trim(),
        name: form.name.trim(),
        kind: form.kind,
        scope: form.scope,
        role: form.role,
        lineCode: form.scope === 'workshop' ? '' : form.lineCode,
        gatewayId: form.gatewayId,
        slave: form.slave,
        register: form.register,
        // ⚠ 不提交 registers：它是后端由 dataType 推导的镜像列（手填会与读取长度冲突）
        dataType: form.dataType,
        byteOrder: form.byteOrder,
        funcCode: form.funcCode,
        wordOrder: form.wordOrder,
        urlPath: form.urlPath.trim(),
        params,
        valuePath: form.valuePath.trim(),
        timePath: form.timePath.trim(),
        factor: form.scale,
        unit: form.unit,
        maxHourly: form.maxHourly,
        enabled: form.enabled,
        remark: form.remark,
        assetNo: form.assetNo,
        installDate: form.installDate
      },
      editingId.value ?? undefined
    );
    ElMessage.success(editingId.value ? '已保存，下一轮采集生效' : '已新增，下一轮采集生效');
    drawer.value = false;
    tableRef.value?.reload();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    saving.value = false;
  }
}

/* ── 试采：现场调参主力 ───────────────────────────────── */
const testing = ref<number | null>(null);
const testVisible = ref(false);
const testResult = ref<{ title: string; ok: boolean; detail: string; raw: string }>({
  title: '',
  ok: false,
  detail: '',
  raw: ''
});

async function doTest(row: MeterPoint) {
  testing.value = row.id;
  try {
    const res = await adminApi.testMeter(row.id);
    testResult.value = {
      title: `${row.code} ${row.name}`,
      ok: res.ok,
      detail: res.detail,
      raw: res.raw || ''
    };
    if (res.ok) tableRef.value?.reload();
  } catch (e) {
    testResult.value = { title: row.code, ok: false, detail: (e as Error).message, raw: '' };
  } finally {
    testing.value = null;
    testVisible.value = true;
  }
}

/* ── 删除 ─────────────────────────────────────────────── */
async function doDelete(row: MeterPoint) {
  try {
    await ElMessageBox.confirm(
      `删除计量点「${row.code} ${row.name}」？历史读数与用量会一并删除，且不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    );
  } catch {
    return;
  }
  try {
    await adminApi.deleteMeter(row.id);
    ElMessage.success('已删除');
    tableRef.value?.reload();
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

/* ── 初始化 ───────────────────────────────────────────── */
void (async () => {
  try {
    gateways.value = await adminApi.getGateways();
  } catch {
    /* 网关取不到不影响列表展示，只是新增时选不了 */
  }
  try {
    // 组织节点：所属节点下拉的数据源（产线 + 辅助计量单元）
    // ⚠ 分页结构的字段是 `list`（不是 items）；pageSize 给足，避免节点多于默认 10 条时被截断
    nodes.value = (await adminApi.getLines({ page: 1, pageSize: 200 })).list;
  } catch {
    /* 节点取不到时下拉为空，提交前会有「请选择所属节点」提示，不会静默存成错误归属 */
  }
})();
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button @click="tableRef?.reload(true)"><el-icon><Refresh /></el-icon>&nbsp;重新加载</el-button>
        <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新增点位</el-button>
      </template>
    </PageHeader>

    <el-alert
      v-if="banner.show"
      type="warning"
      :closable="false"
      show-icon
      class="mb-16"
      :title="banner.text"
    />

    <TablePage
      ref="tableRef"
      :columns="columns"
      :fetch="adminApi.getMeters"
      :extra-query="extraQuery"
      keyword-placeholder="点位编号 / 名称 / 地址"
      :op-width="180"
      :max-height="520"
    >
      <template #filters>
        <el-select v-model="kind" placeholder="全部类别" clearable style="width: 120px">
          <el-option label="电" value="elec" />
          <el-option label="水" value="water" />
          <el-option label="汽" value="steam" />
        </el-select>
        <el-select v-model="gatewayCode" placeholder="全部网关" clearable style="width: 200px">
          <el-option v-for="g in gateways" :key="g.code" :label="`${g.code} ${g.name}`" :value="g.code" />
        </el-select>
        <el-select v-model="status" placeholder="全部状态" clearable style="width: 120px">
          <el-option label="在线" value="online" />
          <el-option label="离线" value="offline" />
          <el-option label="故障" value="fault" />
        </el-select>
      </template>

      <template #col-status="{ row }">
        <el-tooltip v-if="!row.lastOk && row.lastError" :content="row.lastError" placement="top">
          <el-tag :type="STATUS_MAP[row.status]?.type ?? 'info'" size="small">
            {{ STATUS_MAP[row.status]?.text ?? row.status }}
          </el-tag>
        </el-tooltip>
        <el-tag v-else :type="STATUS_MAP[row.status]?.type ?? 'info'" size="small">
          {{ STATUS_MAP[row.status]?.text ?? row.status }}
        </el-tag>
      </template>

      <template #op="{ row }">
        <el-button link type="primary" :loading="testing === row.id" @click="doTest(row)">试采</el-button>
        <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
        <el-button link type="danger" @click="doDelete(row)">删除</el-button>
      </template>
    </TablePage>

    <!-- ══════ 新增 / 编辑抽屉 ══════ -->
    <el-drawer v-model="drawer" :title="editingId ? '编辑计量点位' : '新增计量点位'" size="620px">
      <el-form label-width="106px" label-position="right">
        <el-divider content-position="left">基本信息</el-divider>
        <el-form-item label="点位编号" required>
          <el-input v-model="form.code" placeholder="如 E-A1-01，全局唯一" />
        </el-form-item>
        <el-form-item label="点位名称" required>
          <el-input v-model="form.name" placeholder="如 一线生产线电表" />
        </el-form-item>
        <el-form-item label="能源类型">
          <el-radio-group v-model="form.kind" @change="form.unit = UNIT_BY_KIND[form.kind]">
            <el-radio-button value="elec">电</el-radio-button>
            <el-radio-button value="water">水</el-radio-button>
            <el-radio-button value="steam">汽</el-radio-button>
          </el-radio-group>
          <span class="form-hint">当前：{{ CATEGORY_BY_KIND[form.kind] }}</span>
        </el-form-item>
        <el-form-item label="归属层级">
          <el-radio-group v-model="form.scope">
            <el-radio-button value="line">产线级</el-radio-button>
            <el-radio-button value="workshop">车间总表</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.scope === 'line'" label="所属节点">
          <!-- v1.5.2（B-541）：辅机 / 调配 / 瓶胚的电表挂在 aux 节点上，
               下拉必须覆盖「产线 + 辅助计量单元」，否则现场选不到归属 -->
          <el-select
            v-model="form.lineCode"
            filterable
            style="width: 100%"
            placeholder="选择产线或辅助计量单元"
          >
            <el-option-group v-for="grp in nodeGroups" :key="grp.label" :label="grp.label">
              <el-option
                v-for="n in grp.items"
                :key="n.code"
                :label="`${n.code} ${n.name}`"
                :value="n.code"
              />
            </el-option-group>
          </el-select>
          <span class="form-hint">辅机 / 调配 / 瓶胚的电表选对应的辅助计量单元</span>
        </el-form-item>
        <el-form-item label="计量角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option label="生产线主表" value="line" />
            <el-option label="辅机（吹瓶机 / 冰水机）" value="aux" />
            <el-option label="车间总表" value="total" />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">采集网关</el-divider>
        <el-form-item label="所属网关" required>
          <el-select v-model="form.gatewayId" placeholder="选择网关" style="width: 100%">
            <el-option
              v-for="g in gateways"
              :key="g.id"
              :label="`${g.code} ${g.name}（${g.endpoint}）`"
              :value="g.id"
            />
          </el-select>
        </el-form-item>

        <!-- 串口服务器：Modbus RTU over TCP -->
        <template v-if="isModbus">
          <el-form-item label="从站号" required>
            <el-input-number v-model="form.slave" :min="1" :max="247" controls-position="right" />
            <span class="form-hint">485 总线上的表地址，填错会读到「无响应」</span>
          </el-form-item>
          <el-form-item label="寄存器地址" required>
            <el-input-number v-model="form.register" :min="0" :max="65535" controls-position="right" />
            <span class="form-hint">累计值起始地址（现场电表多为 1000，市政水表为 68）</span>
          </el-form-item>
          <!-- ★ v1.5.3：数据类型决定「拼出整数还是浮点」与「读几个寄存器」 -->
          <el-form-item label="数据类型" required>
            <el-select v-model="form.dataType" style="width: 100%">
              <el-option
                v-for="opt in METER_DATA_TYPE_OPTIONS"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="寄存器数量">
            <!-- 推导值：由「数据类型」决定，只读展示（后端保存时同步），避免与读取长度冲突 -->
            <el-input :model-value="String(registersOf(form.dataType))" disabled style="width: 140px" />
            <span class="form-hint">由数据类型推导，无需手填</span>
          </el-form-item>
          <el-form-item label="功能码">
            <el-select v-model="form.funcCode" style="width: 200px">
              <el-option :value="3" label="03 读保持寄存器（常用）" />
              <el-option :value="4" label="04 读输入寄存器" />
            </el-select>
          </el-form-item>
          <el-form-item label="字序 / 字节序">
            <el-select v-model="form.wordOrder" style="width: 170px">
              <el-option value="big" label="高字在前（ABCD，常用）" />
              <el-option value="little" label="低字在前（CDAB）" />
            </el-select>
            <el-select v-model="form.byteOrder" style="width: 170px; margin-left: 8px">
              <el-option value="big" label="寄存器内大端（AB）" />
              <el-option value="little" label="寄存器内小端（BA）" />
            </el-select>
            <span class="form-hint">现场电表与市政水表均为「高字在前 + 大端」</span>
          </el-form-item>
        </template>

        <!-- HTTP API -->
        <template v-else>
          <el-form-item label="接口路径" required>
            <el-input v-model="form.urlPath" placeholder="如 /api/v1/device/data" />
          </el-form-item>
          <el-form-item label="接口参数">
            <el-input v-model="form.paramsText" type="textarea" :rows="2" placeholder='JSON，如 {"cid": 135}' />
          </el-form-item>
          <el-form-item label="累计值路径">
            <el-input v-model="form.valuePath" placeholder="如 data.0.device_data.var1" />
          </el-form-item>
          <el-form-item label="采集时间路径">
            <el-input v-model="form.timePath" placeholder="如 data.0.collect_time" />
          </el-form-item>
        </template>

        <el-divider content-position="left">换算与限值</el-divider>
        <el-form-item label="倍率">
          <el-input-number v-model="form.scale" :step="0.1" :precision="4" controls-position="right" />
          <span class="form-hint">原始值 × 倍率 = 工程量（现场电表为 0.1）</span>
        </el-form-item>
        <el-form-item label="单位">
          <el-input v-model="form.unit" style="width: 140px" />
        </el-form-item>
        <el-form-item label="小时用量上限">
          <el-input-number v-model="form.maxHourly" :min="0" controls-position="right" />
          <span class="form-hint">超过即判为异常尖峰、不计入合计；留空表示不判</span>
        </el-form-item>

        <el-divider content-position="left">台账与备注</el-divider>
        <el-form-item label="资产编号">
          <el-input v-model="form.assetNo" placeholder="现场贴的资产标签号" />
        </el-form-item>
        <el-form-item label="安装日期">
          <el-input v-model="form.installDate" placeholder="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
          <span class="form-hint">停用后该点位不参与采集与统计</span>
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

    <!-- ══════ 试采结果 ══════ -->
    <el-dialog v-model="testVisible" width="620px" :title="`试采结果 · ${testResult.title}`">
      <el-result
        :icon="testResult.ok ? 'success' : 'error'"
        :title="testResult.ok ? '读取成功' : '读取失败'"
        :sub-title="testResult.detail"
      />
      <el-descriptions v-if="testResult.raw" :column="1" border size="small">
        <el-descriptions-item label="原始报文">{{ testResult.raw }}</el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button type="primary" @click="testVisible = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.form-hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
