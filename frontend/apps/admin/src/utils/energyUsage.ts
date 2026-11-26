/**
 * useEnergyUsage —— 电 / 水 / 汽三个计量页共用的「按区间查询」逻辑
 *
 * 为什么抽出来：这三页的筛选、取数、明细行、图表是同一件事，只差能源类型与单位。
 * 原来各写一份，结果三页的明细表**全都恒空**（B-519：TablePage 在快照返回前就取完数，
 * 之后没有任何机制再取一次），修一处要改三处。
 *
 * 口径全部走 `/api/energy/range` —— 与后台「用量查询」页、大屏同一份实现，
 * 三处数字必然一致（同一指标只能有一个来源）。
 *
 * ── v1.5.2 三级筛选 ──────────────────────────────────────────
 * 筛选维度从「线别」改为 **车间 → 用能类型 → 设备**（分类树来自
 * `/api/energy/meter-categories`）。原因见 `MeterCategoryWorkshop` 的注释：
 * 辅机 / 瓶胚 / 调配这些表在接口表里根本没有「线别」。
 *
 * 选择语义（三级联动，上级变则下级清空）：
 *   · 只选车间            → 该车间全部电表（车间合计）
 *   · 选到用能类型        → 该类型下的表合计
 *   · 选到具体设备/节点   → 绑定设备 / 设备分组 / 节点 / 单表（焦点通道）
 *
 * ── v1.7.4 第三级拆分 ─────────────────────────────────────────
 * 该能源在「计量绑定」里配置了设备后，第三级完全按绑定树展示：
 *   · 全部设备（该类型所有设备计量点去重合计）；
 *   · 单个设备（右侧勾选的一组表）；
 *   · 单块仪表（被该类型设备引用到的物理表）。
 * 没有配置绑定的能源仍按原始台账兜底：多节点按产线，单节点按设备名。
 * 这正是现场要求的「对应设备选择对应仪表的用量」：设备合计与单表都能选到。
 */
import { computed, onMounted, ref, watch, type Ref } from 'vue';
import { ElMessage } from 'element-plus';
import {
  energyApi,
  lastClosedHour,
  productionDayStart,
  type MeterCategoryBinding,
  type MeterCategoryDevice,
  type MeterCategoryWorkshop,
  type UsageRangeResult
} from '@eep/shared';
import { energyUsageOption, UNIT_COLOR, type EnergyUsageSeries } from './charts';

export type UsageEnergy = 'electric' | 'water' | 'steam';
export type UsageGranularity = 'hour' | 'day' | 'month';

export interface UsageRow {
  id: number;
  label: string;
  usageText: string;
  deltaText: string;
  deltaColor: string;
  quality: string;
  /** v1.8.1：产量（标箱优先）与单耗 */
  outputText: string;
  unitText: string;
}

const META: Record<UsageEnergy, { name: string; unit: string; color: string; perLine: boolean; emptyHint: string; kind: 'elec' | 'water' | 'steam' }> = {
  electric: { name: '用电', unit: 'kWh', color: '#1664ff', perLine: true, emptyHint: '本区间电表无可信差值', kind: 'elec' },
  water: { name: '用水', unit: 't', color: '#387bff', perLine: false, emptyHint: '车间进水总表未接入', kind: 'water' },
  steam: { name: '用汽', unit: 't', color: '#bd7e00', perLine: false, emptyHint: '蒸汽总表本区间无数据', kind: 'steam' }
};

/** 本地时间字符串。⚠ 不能用 toISOString（UTC，东八区早上会得到昨天） */
function fmt(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/**
 * 最后一个**已结算**的整点。
 *
 * 小时用量的定义是 `usage(H) = read(H+1:00) − read(H:00)` —— 要等下一个整点采到端点
 * 读数才算得出来，所以「现在」所在的这个小时**永远查不到**：15 点时最大可查 14 点。
 *
 * ⚠ 把「现在」当区间终点会让接口多返回一个恒为空的时段，并把它算进「缺数」
 *   （现场表现为「共 16 个时段，其中 1 个时段缺数」，其实那格本不该出现）。v1.4.4 修复。
 *
 * 注意：**只适用于小时用量的页面**（用量查询 / 电 / 水 / 汽）。
 * 「仪表读数查询」查的是 energy_raw 原始读数，实时都有，终点仍应取「现在」。
 */
export function closedHour(): Date {
  return lastClosedHour();
}

/** 已结算小时所属生产日的 06:00；06:00—06:59 自动退到前一天，避免起点跑到终点后面 */
function anchorDayStart(): Date {
  return productionDayStart(closedHour());
}

/** 快捷区间的起点：0 表示「当前生产日 06:00」（以已结算小时所属生产日为准） */
export function usageQuickStart(hours: number): string {
  if (hours <= 0) return fmt(anchorDayStart());
  return fmt(new Date(closedHour().getTime() - hours * 3600000));
}

/** 小时用量页面的区间终点：一律取最后一个已结算整点 */
export function closedHourText(): string {
  return fmt(closedHour());
}

export interface UsageSelectionOption {
  /** 编码值：bindingType:<id> / binding:<id> / node:<code> / device:<name> / meter:<code> */
  value: string;
  name: string;
  /** 下拉里显示的完整文案（含「绑定 N 块表」等说明） */
  label: string;
  /** 该选项包含的计量点数 */
  count: number;
  kind: 'binding' | 'bindingType' | 'node' | 'device' | 'meter';
  /** 绑定设备由哪几块表组成（直接显示在提示里） */
  detail?: string;
}

export interface UsageSelectionGroup {
  label: string;
  items: UsageSelectionOption[];
}

/** 第三级下拉值 → 后端查询参数。兼容旧值（没有前缀时按节点编码处理）。 */
function selectionParams(value: string): {
  binding?: number;
  bindingType?: number;
  node?: string;
  device?: string;
  meter?: string;
} {
  if (!value) return {};
  if (value.startsWith('bindingType:')) {
    return { bindingType: Number(value.slice('bindingType:'.length)) };
  }
  if (value.startsWith('binding:')) return { binding: Number(value.slice('binding:'.length)) };
  if (value.startsWith('node:')) return { node: value.slice('node:'.length) };
  if (value.startsWith('device:')) return { device: value.slice('device:'.length) };
  if (value.startsWith('meter:')) return { meter: value.slice('meter:'.length) };
  return { node: value };
}

export function useEnergyUsage(energy: UsageEnergy) {
  const meta = META[energy];

  const range = ref<[string, string]>([usageQuickStart(0), closedHourText()]);
  const granularity = ref<UsageGranularity>('hour');
  const loading = ref(false);
  const data = ref<UsageRangeResult | null>(null);
  /** v1.8.1：图表 / 明细显示哪些指标 */
  const showUsage = ref(true);
  const showOutput = ref(true);
  const showUnit = ref(true);

  // ── 三级筛选状态（车间 → 用能类型 → 设备）──────────────────
  /** 分类树；只含本能源类型（电页不会看到水表） */
  const tree = ref<MeterCategoryWorkshop[]>([]);
  const treeLoading = ref(false);
  /** ''=不限（全部车间，当前只有一个）；否则为车间编码 */
  const workshop = ref('');
  /** ''=该车间全部；否则用能类型名（如「生产线用电」） */
  const usageType = ref('');
  /**
   * ''=该类型全部；否则是**编码后的选择值**：
   *   bindingType:<id> 绑定类型全部设备（去重合计）
   *   binding:<id>     绑定树里的单个设备
   *   node:<code>      原始模式：组织节点（多产线时按节点看整条线）
   *   device:<name>    原始模式：单节点下的设备分组
   *   meter:<code>     单块仪表
   *
   * 兼容旧值：没有前缀的字符串按节点编码处理（历史页面状态 / 手工链接）。
   */
  const node = ref('');

  /** 当前选中车间下的类型列表 */
  const typeOptions = computed(() => {
    const ws = tree.value.find((w) => w.workshopCode === workshop.value) ?? tree.value[0];
    return ws?.types ?? [];
  });

  const activeType = computed(
    () => typeOptions.value.find((x) => x.usageType === usageType.value) ?? null
  );

  /** 当前选中类型下的绑定设备 */
  const bindings = computed<MeterCategoryBinding[]>(() => activeType.value?.bindings ?? []);

  /** 当前选中类型下的物理计量点 */
  const deviceOptions = computed<MeterCategoryDevice[]>(() => activeType.value?.devices ?? []);

  /**
   * 第三级选项 —— v1.7.4 起分三组：
   *
   *  绑定树模式（该能源有绑定配置）：
   *    ① 全部设备 / 单个绑定设备；
   *    ② 单块仪表（被该类型设备引用到的物理表）。
   *  原始模式（该能源没有绑定配置）：
   *    ① 按节点或 `deviceName` 生成的设备分组；
   *    ② 单块仪表。
   *
   * ⚠ 第三级选项的 `value` 必须带前缀：同一字符串既可能是节点编码、也可能是
   *   设备名或表编码，不带前缀就无法判断该传 `node` 还是 `device`（B-535 同族：
   *   认不出的参数被静默忽略，界面看着有数，实际是另一个口径）。
   */
  const selectionGroups = computed<UsageSelectionGroup[]>(() => {
    const groups: UsageSelectionGroup[] = [];
    const devices = deviceOptions.value;

    // ── 绑定树模式 ──
    // 该能源已经在绑定页配置了「设备 + 启用表」时，第三级只认绑定树：
    // 设备合计、全部设备合计、单块仪表。不再混入按物理表台账生成的
    // 节点/设备分组 —— 两套分类同时出现时，选哪个都像，口径却可能不同。
    if (activeType.value?.bindingDriven) {
      const items: UsageSelectionOption[] = [];
      if (activeType.value.id != null && bindings.value.length) {
        items.push({
          value: `bindingType:${activeType.value.id}`,
          name: '全部设备',
          label: `全部设备 · ${devices.length} 块表`,
          count: devices.length,
          kind: 'bindingType',
          detail: devices.map((d) => d.name).join('、')
        });
      }
      for (const b of bindings.value) {
        items.push({
          value: `binding:${b.id}`,
          name: b.deviceName,
          label: `${b.deviceName} · 绑定 ${b.meterCount} 块表`,
          count: b.meterCount,
          kind: 'binding',
          detail: b.meterNames?.join('、') ?? ''
        });
      }
      if (items.length) groups.push({ label: '设备', items });
      if (devices.length) {
        groups.push({
          label: '单块仪表',
          items: devices.map((d) => ({
            value: `meter:${d.code}`,
            name: `${d.name} · ${d.code}`,
            label: `${d.name} · ${d.code}`,
            count: 1,
            kind: 'meter'
          }))
        });
      }
      return groups;
    }

    // ── 原始模式（该能源还没有绑定配置）──
    if (bindings.value.length) {
      groups.push({
        label: '已绑定设备',
        items: bindings.value.map((b) => ({
          value: `binding:${b.id}`,
          name: b.deviceName,
          label: `${b.deviceName} · 绑定 ${b.meterCount} 块表`,
          count: b.meterCount,
          kind: 'binding',
          detail: b.meterNames?.join('、') ?? ''
        }))
      });
    }

    if (devices.length) {
      const nodes = new Map<string, { code: string; name: string; count: number }>();
      for (const d of devices) {
        const cur = nodes.get(d.nodeCode);
        if (cur) cur.count += 1;
        else nodes.set(d.nodeCode, { code: d.nodeCode, name: d.nodeName, count: 1 });
      }

      // 只有「单节点 + 每块表都有 device_name」才能按设备名分组：
      // 后端 `device=` 过滤的是 `device_name` 字段，拿计量点名称兜底会筛不到表。
      const canGroupByDevice = nodes.size === 1 && devices.every((d) => !!d.deviceName);
      if (!canGroupByDevice) {
        // 多节点 = 多条产线（或跨节点类型）：按节点看「整条线/整个单元」
        groups.push({
          label: '设备 / 节点（未绑定）',
          items: [...nodes.values()].map((n) => ({
            value: `node:${n.code}`,
            name: n.name,
            label: n.count > 1 ? `${n.name} · ${n.count} 块表` : n.name,
            count: n.count,
            kind: 'node'
          }))
        });
      } else {
        // 单节点 = 辅机 / 水 / 汽：按设备名分组（冰水、高压…）
        const names = new Map<string, { name: string; count: number }>();
        for (const d of devices) {
          const key = d.deviceName;
          const cur = names.get(key);
          if (cur) cur.count += 1;
          else names.set(key, { name: key, count: 1 });
        }
        groups.push({
          label: '设备（未绑定）',
          items: [...names.values()].map((n) => ({
            value: `device:${n.name}`,
            name: n.name,
            label: n.count > 1 ? `${n.name} · ${n.count} 块表` : n.name,
            count: n.count,
            kind: 'device'
          }))
        });
      }

      groups.push({
        label: '单块仪表',
        items: devices.map((d) => ({
          value: `meter:${d.code}`,
          name: `${d.name} · ${d.code}`,
          label: `${d.name} · ${d.code}`,
          count: 1,
          kind: 'meter'
        }))
      });
    }

    return groups;
  });

  /** 扁平选项 —— 用于按 value 反查名称、生成选中提示 */
  const nodeOptions = computed(() => selectionGroups.value.flatMap((g) => g.items));

  /** 当前选中的选项 */
  const selectedOption = computed(
    () => nodeOptions.value.find((x) => x.value === node.value) ?? null
  );

  /** 当前选择实际包含的计量点 —— 让「这个设备由哪几块表组成」直接可见 */
  const selectedMeters = computed<{ names: string[]; codes: string[] }>(() => {
    const opt = selectedOption.value;
    if (!opt) return { names: [], codes: [] };
    if (opt.kind === 'binding') {
      const b = bindings.value.find((x) => `binding:${x.id}` === opt.value);
      return { names: b?.meterNames ?? [], codes: b?.meterCodes ?? [] };
    }
    if (opt.kind === 'bindingType') {
      return {
        names: deviceOptions.value.map((d) => d.name),
        codes: deviceOptions.value.map((d) => d.code)
      };
    }
    if (opt.kind === 'meter') {
      const d = deviceOptions.value.find((x) => `meter:${x.code}` === opt.value);
      return d ? { names: [d.name], codes: [d.code] } : { names: [], codes: [] };
    }
    const matched = deviceOptions.value.filter((d) =>
      opt.kind === 'node'
        ? d.nodeCode === opt.value.slice('node:'.length)
        : (d.deviceName || d.name) === opt.value.slice('device:'.length)
    );
    return { names: matched.map((d) => d.name), codes: matched.map((d) => d.code) };
  });

  /** 载入分类树；只拉本页能源类型，避免电页出现水表 */
  async function loadTree() {
    treeLoading.value = true;
    try {
      const res = await energyApi.getMeterCategories({ kind: meta.kind });
      tree.value = res.tree;
      // 默认选中第一个车间，让三级下拉一开始就有内容可选
      if (!workshop.value && res.tree.length) workshop.value = res.tree[0].workshopCode;
    } catch (e) {
      ElMessage.error((e as Error).message);
      tree.value = [];
    } finally {
      treeLoading.value = false;
    }
  }

  // 上级变化时清空下级 —— 否则会拿「A1 线的用能类型」去查另一个车间，
  // 后端筛出空集，界面看起来像「这个车间没有数据」。
  // flush: 'sync' 必须保留：视图里的 @change="load" 会在 v-model 更新后立刻执行，
  // 若清空下级是异步的，load 会拿「新车间 + 旧类型/旧设备」去查 —— 后端筛出空集，
  // 界面看起来像「这个车间没有数据」。
  watch(
    workshop,
    () => {
      usageType.value = '';
      node.value = '';
    },
    { flush: 'sync' }
  );
  watch(
    usageType,
    () => {
      node.value = '';
    },
    { flush: 'sync' }
  );

  /** 是否处于「焦点」通道：选了类型或节点就是看具体设备，只出一条序列 */
  const focused = computed(() => !!(usageType.value || node.value));

  /**
   * 当前选择的中文描述 —— 放在图表标题/提示里，
   * 让人一眼知道「这张图是哪几个点位的」，而不是只能靠下拉回看。
   */
  const selectionLabel = computed(() => {
    const ws = tree.value.find((w) => w.workshopCode === workshop.value);
    const parts = [ws?.workshop ?? '全部车间'];
    if (usageType.value) parts.push(usageType.value);
    if (node.value) {
      const opt = selectedOption.value;
      if (!opt) {
        parts.push(node.value);
      } else if (opt.kind === 'binding') {
        parts.push(`${opt.name}（绑定 ${opt.count} 块表）`);
      } else if (opt.kind === 'bindingType') {
        parts.push(`${opt.name}（${opt.count} 块表）`);
      } else if (opt.count > 1) {
        parts.push(`${opt.name}（${opt.count} 块表）`);
      } else {
        parts.push(opt.name);
      }
    }
    return parts.join(' · ');
  });

  async function load() {
    if (!range.value?.[0] || !range.value?.[1]) {
      ElMessage.warning('请选择起止时间');
      return;
    }
    loading.value = true;
    try {
      data.value = await energyApi.getRange({
        from: range.value[0],
        to: range.value[1],
        granularity: granularity.value,
        // 焦点通道由后端按点位集合出数，scope 只影响非焦点时的序列构成
        scope: focused.value ? 'all' : 'workshop',
        kind: meta.kind,
        workshop: workshop.value || undefined,
        usageType: usageType.value || undefined,
        // 绑定树模式下，第三级不选具体设备 = 看该类型全部设备；
        // 选中设备时由 selectionParams 覆盖为 binding。
        bindingType:
          !node.value && activeType.value?.bindingDriven && activeType.value.id != null
            ? activeType.value.id
            : undefined,
        ...selectionParams(node.value)
      });
    } catch (e) {
      ElMessage.error((e as Error).message);
      data.value = null;
    } finally {
      loading.value = false;
    }
  }

  onMounted(async () => {
    await loadTree();
    await load();
  });

  /** 本能源的逐桶用量 */
  const values = computed<(number | null)[]>(() => {
    const d = data.value;
    if (!d) return [];
    if (energy === 'electric') {
      // 焦点通道下 series[0] 就是唯一那条；非焦点时取车间合计
      const wantKey = d.line ?? 'workshop';
      const s = d.series.find((x) => x.key === wantKey) ?? d.series[0];
      return s?.values ?? [];
    }
    return energy === 'water' ? d.utility.water : d.utility.steam;
  });

  /**
   * 明细行：时间 / 用量 / 较上一时段 / 数据质量。
   * 缺数一律 `--`，且**不参与环比** —— 拿缺数格当 0 去比会凭空造出一个 -100%。
   */
  /** v1.8.1：产量序列（标箱优先） */
  const outputs = computed<(number | null)[]>(() => data.value?.production?.values ?? []);
  const outputUnit = computed(() => data.value?.production?.unit ?? '标箱');
  const unitValues = computed<(number | null)[]>(() => data.value?.unitConsumption?.values ?? []);
  const unitName = computed(() => data.value?.unitConsumption?.unit ?? '');

  const rows = computed<UsageRow[]>(() => {
    const d = data.value;
    if (!d) return [];
    const list: UsageRow[] = values.value.map((v, i) => {
      const prev = i > 0 ? values.value[i - 1] : null;
      const delta = v !== null && prev !== null && prev > 0 ? ((v - prev) / prev) * 100 : null;
      const out = outputs.value[i] ?? null;
      const unit = unitValues.value[i] ?? null;
      return {
        id: i,
        label: d.labels[i],
        usageText: v === null || v === undefined ? '--' : String(v),
        deltaText: delta === null ? '--' : `${delta > 0 ? '+' : ''}${delta.toFixed(3)}%`,
        deltaColor: delta === null ? '#86909c' : delta > 0 ? '#d7312a' : '#2a814b',
        quality: d.quality?.[i] ?? '',
        outputText: out === null ? '--' : String(Math.round(out)),
        unitText: unit === null ? '--' : unit.toFixed(3)
      };
    });
    return list.reverse(); // 最新在上
  });

  const totals = computed(() => {
    const d = data.value;
    if (!d) return null;
    return {
      kwh: d.totals.kwh,
      waterT: d.totals.waterT,
      steamT: d.totals.steamT,
      buckets: d.bucketCount,
      from: (d as UsageRangeResult & { fromHour?: string }).fromHour ?? d.from,
      to: (d as UsageRangeResult & { toHour?: string }).toHour ?? d.to
    };
  });

  const missingBuckets = computed(() => values.value.filter((v) => v === null || v === undefined).length);

  /**
   * 整段没数时给一句话说明，避免「图是空的」被读成「系统坏了」。
   *
   * ⚠ 不能无条件用写死文案：水页那句是「车间进水总表未接入」，
   *   可表一旦接好、只是本区间缺采，这句话就会把人支去做错的事
   *   （去查接线，而真正该查的是链路）。所以优先按 coverage 判定。
   */
  const emptyHint = computed(() => {
    const v = values.value;
    if (!v.length) return '';
    const allMissing = v.every((x) => x === null || x === undefined);
    if (!allMissing) return '';
    const cov = data.value?.coverage;
    if (cov && cov.connected === 0) return `${meta.name}计量点尚未接入 —— 请接线 / 补全配置`;
    return allMissing && cov?.missing.length ? `${meta.name}本区间无可信数据（已接入点位缺采）` : meta.emptyHint;
  });

  /**
   * 覆盖度说明 —— 把「多少块表在算这个数」和「还有几块表根本没接」说清。
   *
   * 口径变更：合计现在只按**已接入**的表算，所以留空只可能是缺采（要查链路），
   * 不再可能是「某块表压根没接线」。未接入的表单独列一句，提示去接线 / 补 token，
   * 它不参与合计、也不该被当成故障。
   */
  const coverageHint = computed(() => {
    const cov = data.value?.coverage;
    if (!cov || cov.total === 0) return '';
    const parts: string[] = [];
    if (cov.missing.length) {
      const shown = cov.missing.slice(0, 6).join('、');
      const more = cov.missing.length > 6 ? ` 等 ${cov.missing.length} 个` : '';
      parts.push(
        `本区间 ${cov.covered}/${cov.connected} 个已接入计量点有数据，合计按「全部可信才算数」口径留空。缺采：${shown}${more}`
      );
    } else {
      parts.push(`本区间 ${cov.covered}/${cov.connected} 个已接入计量点均有数据`);
    }
    if (cov.unconnected.length) {
      const shown = cov.unconnected.slice(0, 6).join('、');
      const more = cov.unconnected.length > 6 ? ` 等 ${cov.unconnected.length} 个` : '';
      parts.push(`未接入（不参与合计）：${shown}${more}`);
    }
    return parts.join('；');
  });

  const chartOption = computed(() => {
    const d = data.value;
    if (!d) return null;
    // 焦点通道下后端已给出精确的序列名（含车间/类型/节点），优先用它，
    // 否则「车间用电」这种笼统名字会让人以为看的是全车间。
    const seriesName = focused.value ? (d.series[0]?.name ?? selectionLabel.value) : `车间${meta.name}`;
    const series: EnergyUsageSeries[] = [];
    if (showUsage.value) {
      series.push({
        key: energy,
        name: seriesName,
        unit: meta.unit,
        color: meta.color,
        data: values.value,
        emptyHint: meta.emptyHint
      });
    }
    if (showOutput.value) {
      series.push({
        key: 'output',
        name: `产量 · ${d.production?.scope === 'line' ? '所选产线' : '车间合计'}`,
        unit: outputUnit.value,
        color: '#2a814b',
        data: outputs.value,
        type: 'bar',
        emptyHint: '本期没有产量数据（PLC / 标箱产量未接入）'
      });
    }
    if (showUnit.value) {
      series.push({
        key: 'unit',
        name: '单耗',
        unit: unitName.value || `${meta.unit}/${outputUnit.value}`,
        // v1.8.6：单耗曲线统一紫罗兰，和用量（蓝/黄）、产量（绿柱）区分开
        color: UNIT_COLOR,
        data: unitValues.value,
        emptyHint: '产量为 0 或未接入，单耗无法计算'
      });
    }
    return energyUsageOption(d.labels, series, { labelInterval: d.labels.length > 24 ? 2 : 'auto' });
  });

  /**
   * 传给 TablePage 的 extraQuery：
   * ⚠ 这里必须带上「数据是否已就绪」—— TablePage 只在挂载与 extraQuery 变化时取数，
   *   不带它就回到 B-519 的老问题（快照还没回来就取完数，之后再也不刷新）。
   * ⚠ 也必须带上筛选条件 —— 否则改了三级下拉不点查询就不生效（B-532 同族）。
   */
  const extraQuery: Ref<Record<string, unknown>> = computed(() => ({
    ready: data.value !== null,
    buckets: data.value?.bucketCount ?? 0,
    energy,
    workshop: workshop.value,
    usageType: usageType.value,
    node: node.value,
    gran: granularity.value,
    metrics: `${showUsage.value ? 1 : 0}${showOutput.value ? 1 : 0}${showUnit.value ? 1 : 0}`
  }));

  return {
    meta,
    range,
    granularity,
    loading,
    data,
    load,
    rows,
    values,
    outputs,
    outputUnit,
    unitValues,
    unitName,
    showUsage,
    showOutput,
    showUnit,
    totals,
    missingBuckets,
    emptyHint,
    coverageHint,
    chartOption,
    extraQuery,
    // 三级筛选
    tree,
    treeLoading,
    workshop,
    usageType,
    node,
    typeOptions,
    deviceOptions,
    selectionGroups,
    nodeOptions,
    selectedOption,
    selectedMeters,
    focused,
    selectionLabel,
    loadTree
  };
}

/** 快捷区间：三页共用，避免每页各写一份 */
export function usageQuickSpans(): { label: string; hours: number; gran: UsageGranularity }[] {
  return [
    { label: '近 6 小时', hours: 6, gran: 'hour' },
    { label: '今日', hours: 0, gran: 'hour' },
    { label: '近 3 日', hours: 72, gran: 'hour' },
    { label: '近 7 日', hours: 168, gran: 'day' },
    { label: '近 30 日', hours: 720, gran: 'day' }
  ];
}

/**
 * 以下两个仅供「仪表读数查询」使用 —— 它查的是 `energy_raw` 原始累计读数，
 * 任何时刻都有最新值，所以区间终点取「现在」是对的。
 * ⚠ 小时用量的页面（用量查询 / 电 / 水 / 汽）不要用它们，改用 `usageQuickStart` +
 *   `closedHourText`，否则会多出一个恒为空的当前小时（见 closedHour 的说明）。
 */
export function hourAgo(hours: number): string {
  const d = new Date();
  d.setHours(d.getHours() - hours);
  if (hours === 0) return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 00:00`;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function nowHourText(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:00`;
}
