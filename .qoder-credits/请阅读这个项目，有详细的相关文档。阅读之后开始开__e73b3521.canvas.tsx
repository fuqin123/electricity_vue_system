import {
  BarChart,
  Callout,
  ChartComparisonGrid,
  ChartContainer,
  H1,
  LineChart,
  MetricsGrid,
  PieChart,
  ReportSection,
  ReportShell,
  Stack,
  Table,
  Text,
  type MetricItem,
} from "qoder/canvas";

// —— 报告数据契约（由 scripts/lib/breakdown.mjs 产出，plugin 注入）——
interface CatRow {
  key: string;
  label: string;
  tokens: number;
  share: number;
}
interface ToolRow {
  tool: string;
  tokens: number;
  calls: number;
  /** 平均每次调用带来的重发 token；无调用记录时 null（不给 0） */
  perCall?: number | null;
  share: number;
}
interface FileRow {
  attr: string;
  label: string;
  kind: string;
  tool: string | null;
  selfTokens: number;
  reads: number;
  trips: number;
  billed: number;
  /** 单次读取的总代价（含此后每一程重发）；reads=0 时 null */
  perRead?: number | null;
  share: number;
}
interface ReqRow {
  index: number;
  time: string | null;
  ratio: number;
  inputTokens: number;
  /** 代理或转录任一有真值即非 null；两者都没则 null（不写 0） */
  outputTokens?: number | null;
  /** proxy=代理实测 / transcript-tokens=转录 input_tokens / transcript-ratio=转录 ratio×window */
  usageSource?: "proxy" | "transcript-tokens" | "transcript-ratio" | "transcript";
  credits: number;
  originalCredits: number;
  afterCompact: boolean;
}
interface CatChild {
  tool: string | null;
  label: string;
  kind: string | null;
  tokens: number;
  share: number;
  catShare: number;
  trips: number;
}
interface CatDetail {
  key: string;
  label: string;
  tokens: number;
  share: number;
  children: CatChild[];
}
interface SubagentRow {
  agentId: string;
  agentType: string | null;
  description: string | null;
  toolUseId: string | null;
  roundTrips: number;
  billedInputTokens: number;
  peakContextRatio: number;
  credits: number;
  originalCredits: number;
  error: string | null;
}
interface SubagentTotals {
  roundTrips: number;
  billedInputTokens: number;
  credits: number;
  originalCredits: number;
}
interface Subagents {
  scanned: boolean;
  dir: string | null;
  /** 试过的候选目录；用于区分「真没子代理」与「路径没找对」。 */
  probedPaths?: string[];
  /** ok=已汇总 / no-dir=无子代理目录 / dir-empty=目录在但无 agent 文件 / no-usage=有文件但转录无 usage / error=读取抛错 */
  reason?: string;
  count: number;
  items: SubagentRow[];
  totals: SubagentTotals;
  combined: SubagentTotals;
}
/** 逐项可用性：报告里每个数字到底是模型上报的真值、按真值推导、回退默认、手工录入，还是本地根本没有。
 *  v2 旧报告没这个对象，故全部字段可选，读取时一律走默认值（旧报告只可能来自桌面端富转录）。 */
type Avail = "measured" | "derived" | "fallback" | "manual" | "unavailable";
interface Availability {
  credits?: Avail;
  roundTrips?: Avail;
  contextRatio?: Avail;
  tokens?: Avail;
  /** 输出 token：代理或转录 output_tokens 任一有真值即 measured */
  outputTokens?: Avail;
  /** 缓存命中 token：代理或转录 cache_read_input_tokens 任一有真值即 measured */
  cachedTokens?: Avail;
  categoryShare?: Avail;
  toolShare?: Avail;
  fileShare?: Avail;
  systemPrompt?: Avail;
  contextWindow?: Avail;
  compactions?: Avail;
  compactionCost?: Avail;
  model?: Avail;
  title?: Avail;
  toolCalls?: Avail;
  fileReads?: Avail;
  userTurns?: Avail;
  /** 用户压缩阈值：manual 覆盖 → manual；默认 200K → fallback */
  userContextLimit?: Avail;
}
/** 官方 UI 真值（手工录入，来自 .qoder-credits/overrides/<sessionId>.json）。
 *  IDE 端转录不含 usage，本地算不出 Credits，这是唯一的真值通道；绝不覆盖 totals，只并列展示。 */
interface ManualTruth {
  file?: string;
  credits: number | null;
  originalCredits: number | null;
  model: string | null;
  startedAt: string | null;
  endedAt: string | null;
  durationMin: number | null;
  note: string | null;
  /** 手工锁定模型窗口（如 qwen3-max=1000000），优先于 runtime-config / 反推 / fallback */
  contextWindow?: number | null;
  /** 手工指定 Qoder 压缩触发阈值（默认 200000），驱动 peakUserAdvice */
  userContextLimit?: number | null;
  /** 本地 credits ÷ 官方真值。有子代理时拿 combined 比（官方 UI 扣费 = 主链 + 子代理），
   *  否则覆盖率会被系统性低估（实测 ca2f7834：主链比 0.46、combined 比 0.89）。 */
  localCoverage?: number | null;
  /** main=只比主链 / combined=比主链+子代理 */
  localScope?: "main" | "combined";
  /** 参与对账的本地 credits（按 localScope 取 totals.credits 或 subagents.combined.credits） */
  localCredits?: number | null;
}
/** 峰值占比的「该不该压」结论。阈值算在数据层（breakdown.mjs peakAdvice），
 *  这里只管展示——否则 Canvas 与终端各持一套阈值迟早走偏。tone 直接喂 Callout。 */
interface PeakAdvice {
  level: "low" | "mid" | "sweet" | "high" | "over";
  tone: "info" | "success" | "warning" | "danger";
  text: string;
}
/** 一次压缩 = 一笔普通模型调用（整份上下文当 prompt、摘要当 completion），是会话里单笔最贵的开销。
 *  pre/post 是客户端自估口径；proxy 是能在代理日志里唯一对上时回填的供应商实测值，对不上就 null。 */
interface CompactionEvent {
  index: number;
  at: string | null;
  trigger: string | null;
  preTokens: number | null;
  postTokens: number | null;
  messagesSummarized: number | null;
  /** 压缩后首轮的往返序号与实测输入 = 压缩把上下文压到的地板 */
  nextRequestIndex: number | null;
  nextInputTokens: number | null;
  savedTokens: number | null;
  /** 数据层归一后的生效值：有代理实测就用实测，否则是客户端自估。
   *  渲染端直接取这两个，别自己按 proxy 分支——否则会与合计的口径分叉。 */
  effectiveInputTokens?: number | null;
  effectiveOutputTokens?: number | null;
  proxy?: {
    promptTokens: number;
    completionTokens: number;
    cachedTokens: number | null;
    ms: number | null;
    matchedBy: string;
  } | null;
}
interface CompactionCost {
  count: number;
  items: CompactionEvent[];
  /** measured = items 里有几笔拿到了供应商实测（其余为客户端自估） */
  totals: { preTokens: number; postTokens: number; savedTokens: number; measured?: number };
}
/** 链路健康度：代理覆盖率 + 最近记录时间 + 最近若干笔的三方归因计数。
 *  逐笔明细属于排障（CLI --request-log），报告只给一行结论。 */
interface LinkHealth {
  matched: number;
  requests: number;
  coverage: number;
  /** proxy / transcript-tokens / transcript-ratio / mixed / transcript（v3 旧报告）/ null */
  usageSource?: "proxy" | "transcript-tokens" | "transcript-ratio" | "mixed" | "transcript" | null;
  /** 三档拆分：代理实测 / 转录 input_tokens / 转录 ratio×window */
  breakdown?: { proxy: number; transcriptTokens: number; transcriptRatio: number };
  logRecords?: number;
  lastRecordAt?: string | null;
  /** 参与归因的最近笔数；0 = 运行中的代理是旧版或还没记到诊断 */
  recentRequests?: number;
  proxyErrors?: number | null;
  upstreamRejected?: number | null;
  aborted?: number | null;
  truncated?: number | null;
  noUsage?: number | null;
  slowestMs?: number | null;
  /** 被更新流量推翻的那条陈旧拉起失败记录；null = 没有 */
  supersededFailure?: { error: string; port?: number; at?: string | null } | null;
}
interface Report {
  schemaVersion: number;
  generatedAt: string;
  pluginVersion?: string;
  /** 转录来自哪一代客户端：桌面端富转录有 usage 真值，IDE 端精简转录没有。v2 旧报告无此字段。 */
  source?: "desktop-rich" | "ide-lite" | "unknown";
  /** usage 数字的来源：proxy=代理实测 / transcript-tokens=转录 input_tokens 真值 /
   *  transcript-ratio=转录 ratio×window 推导 / mixed=多档混合 / transcript=v3 旧报告兼容。无 usage 时 null。 */
  usageSource?: "proxy" | "transcript-tokens" | "transcript-ratio" | "mixed" | "transcript" | null;
  /** 可直接粘贴执行的插件入口命令（generate.mjs 用 proxy.mjs 的 selfCmd() 注入）。
   *  Windows 上是插件自带启动器的绝对路径，不要求用户装 Node；模板是静态文本，拿不到就只能硬编码。 */
  selfCmd?: string | null;
  /** 链路健康度一行结论（逐笔明细留给 CLI --request-log） */
  linkHealth?: LinkHealth | null;
  /** 代理 join 诊断（lib/proxylog.mjs）+ 配置/状态（generate.mjs 注入） */
  proxy?: {
    matched: number;
    requests: number;
    /** 三档拆分：代理实测 / 转录 input_tokens / 转录 ratio×window */
    breakdown?: { proxy: number; transcriptTokens: number; transcriptRatio: number };
    logPath?: string;
    logExists?: boolean;
    logRecords?: number;
    /** config.json 里 upstream 合法 = 用户已配置代理 */
    configured?: boolean;
    enabled?: boolean;
    port?: number;
    /** 最近一条代理记录的时间；null = 从无流量 */
    lastRecordAt?: string | null;
    /** 已配置但长期零流量（多半已改回官方模型）——软提示可 --stop-proxy */
    dormant?: boolean;
    /** 最近一次自动拉起失败（如端口被占）；ok 时为 null */
    status?: { error: string; port?: number; at?: string | null } | null;
    /** status 那条失败记录是否已被更新的流量推翻（代理在它之后还记到了流量）。
     *  status.json 只写不清，陈旧失败会把用户推去改本来正确的 Base URL，故必须判掉。 */
    statusSuperseded?: boolean;
  };
  availability?: Availability;
  manual?: ManualTruth | null;
  session: {
    id: string | null;
    title: string | null;
    /** custom-title / first-user / session-id —— 令产物文件名可解释 */
    titleSource?: string;
    model: string | null;
    /** runtime-config / manual / unavailable */
    modelSource?: string;
    cwd: string | null;
    turns: number;
    roundTrips: number;
    compactions: number;
    startedAt: string | null;
    endedAt: string | null;
  };
  context: {
    contextWindow: number;
    /** runtime-config=实测 / derived-from-usage=从 usage 反推 / manual=ManualTruth 手工锁定 /
     *  fallback=读不到静默回退 200000 / caller=调用方传入（旧枚举，兼容）。
     *  报告里每个 token 数字都要乘它，回退时必须标出来。 */
    contextWindowSource?: string;
    systemPromptTokens: number;
    netContextTokens: number;
    /** 峰值 = 当前窗口口径：自最近一次压缩起算，压缩边界处归零重新累积 */
    peakContextTokens: number;
    peakContextRatio: number;
    /** 本场历史峰值（跨压缩）；整场没压缩过时与上面相等 */
    peakSessionTokens?: number;
    peakSessionRatio?: number;
    /** 历史峰值是否值得单独交代（与当前窗口峰值相差 ≥1 个百分点），渲染端直接取用不再自判 */
    peakSessionNotable?: boolean;
    /** 峰值占比的「该不该压」结论（对模型窗口）；无有效占比时 null */
    peakAdvice?: PeakAdvice | null;
    /** 用户压缩阈值（Qoder 自动触发点），与模型窗口独立。默认 200000，ManualTruth 可覆盖 */
    userContextLimit?: number;
    /** default=内置 200K / manual=overrides 手写 / config=插件配置 / derived=实测反推 */
    userContextLimitSource?: "default" | "manual" | "config" | "derived";
    /** peakContextTokens ÷ userContextLimit */
    peakUserRatio?: number;
    /** 对用户阈值的「快自动压缩了吗」结论；与 peakAdvice 可矛盾（模型窗口未满但用户阈值已超） */
    peakUserAdvice?: PeakAdvice | null;
    /** v3.2 当前占用头条口径：段内单调递增 ⇒ 峰值≡当前，故以 netContextTokens 作头条，peak 退灰字 */
    currentContextTokens?: number;
    netUserRatio?: number;
    netUserAdvice?: PeakAdvice | null;
    netWindowRatio?: number;
    /** v3.2 自动压缩自校准：本场 trigger=auto 的实际触发点（无需外部配置）与「阈值是否被强制」判定 */
    observedAutoCompactions?: number;
    observedAutoTriggerTokens?: number | null;
    autoTriggerRatio?: number | null;
    thresholdNotEnforced?: boolean;
  };
  totals: {
    billedInputTokens: number;
    netContextTokens: number;
    peakContextTokens: number;
    amplification: number;
    attributedTokens: number;
    coverage: number;
    credits: number;
    originalCredits: number;
    /** 输出 token 总量：仅代理路径有真值，否则 0 且 availability.outputTokens=unavailable */
    outputTokens?: number;
    /** 供应商上下文缓存命中的那部分 prompt：仅代理路径有真值 */
    cachedTokens?: number;
    cachedTrips?: number;
    /** 缓存命中 ÷ 计费输入总量（全局实测约 91.5%：重复叠加的前缀正是缓存的命中对象） */
    cachedShare?: number;
    /** 两个不依赖 usage 的计数，IDE 端占比全缺时仍有可展示的真值 */
    toolCalls?: number;
    fileReads?: number;
  };
  /** credits 的分子到底覆盖了多少：与计费输入总量并排展示时，部分覆盖不说就等于把局部真值当全量。 */
  creditsCoverage?: {
    /** 真正累加进 totals.credits 的往返数 */
    trips: number;
    roundTrips: number;
    /** 这些往返的计费输入之和 */
    tokens: number;
    tokenShare: number;
    /** trips === roundTrips：全覆盖时不必再啰嗦覆盖范围 */
    full: boolean;
  };
  /** 压缩单笔成本（此前只显示「压缩 N 次」，而它是会话里单笔最贵的调用） */
  compactionCost?: CompactionCost;
  byCategory: CatRow[];
  byCategoryDetail: CatDetail[];
  byTool: ToolRow[];
  byFile: FileRow[];
  byRequest: ReqRow[];
  subagents?: Subagents;
  identity: { sumAttributed: number; billedInputTokens: number; absDiff: number; ok: boolean | null };
}

// 注入点：下一行的 REPORT 初值会被 render-canvas.mjs 按整行替换为真实报告 JSON。
const REPORT = {"schemaVersion":3.2,"generatedAt":"2026-09-23T08:52:46.752Z","source":"desktop-rich","usageSource":"transcript-ratio","availability":{"credits":"measured","roundTrips":"measured","contextRatio":"measured","tokens":"derived","outputTokens":"unavailable","cachedTokens":"unavailable","categoryShare":"derived","toolShare":"derived","fileShare":"derived","systemPrompt":"derived","contextWindow":"measured","compactions":"measured","compactionCost":"measured","model":"measured","title":"measured","toolCalls":"measured","fileReads":"measured","userTurns":"measured","userContextLimit":"fallback"},"session":{"id":"e73b3521-68ea-4aa6-a5bc-b2f38467081a","title":"请阅读这个项目，有详细的相关文档。阅读之后开始开","titleSource":"first-user","model":"qfmodel","modelSource":"runtime-config","cwd":"E:\\能源产效管理平台\\electricity_vue_system","turns":8,"roundTrips":247,"compactions":0,"startedAt":"2026-09-23T06:04:12.658Z","endedAt":"2026-09-23T08:52:20.499Z"},"context":{"contextWindow":1000000,"contextWindowSource":"runtime-config","systemPromptTokens":20351,"netContextTokens":341341,"peakContextTokens":341341,"peakContextRatio":0.341341,"peakSessionTokens":341341,"peakSessionRatio":0.341341,"peakSessionNotable":false,"peakAdvice":{"level":"low","tone":"info","text":"低于盈亏线（39%）。此时压缩在 token 上是亏的：省下的还不够付摘要输出加压缩后重读，别压"},"userContextLimit":200000,"userContextLimitSource":"default","peakUserRatio":1.706705,"peakUserAdvice":{"level":"over","tone":"danger","text":"已经超过上下文窗口本身，随时可能被截断或由客户端在最糟的时机自动压缩，立刻手动压一次"},"currentContextTokens":341341,"netUserRatio":1.706705,"netUserAdvice":{"level":"over","tone":"danger","text":"已经超过上下文窗口本身，随时可能被截断或由客户端在最糟的时机自动压缩，立刻手动压一次"},"netWindowRatio":0.341341,"observedAutoCompactions":0,"observedAutoTriggerTokens":null,"autoTriggerRatio":null,"thresholdNotEnforced":true},"totals":{"billedInputTokens":54257794,"netContextTokens":341341,"peakContextTokens":341341,"amplification":158.95,"attributedTokens":54257794,"coverage":1,"credits":143.885,"originalCredits":143.885,"outputTokens":0,"cachedTokens":0,"cachedTrips":0,"cachedShare":0,"toolCalls":273,"fileReads":103},"creditsCoverage":{"trips":247,"roundTrips":247,"tokens":54257794,"tokenShare":1,"full":true},"compactionCost":{"count":0,"items":[],"totals":{"preTokens":0,"postTokens":0,"savedTokens":0,"measured":0}},"proxy":{"matched":0,"requests":247,"breakdown":{"proxy":0,"transcriptTokens":0,"transcriptRatio":247},"logPath":"C:\\Users\\cat\\.qoder-credits-proxy\\usage.jsonl","logExists":false,"logRecords":0,"configured":false,"enabled":true,"port":49787,"lastRecordAt":null,"dormant":false,"status":null,"statusSuperseded":false},"byCategory":[{"key":"tool_result","label":"工具返回","tokens":29166797,"share":0.5375595851170794},{"key":"system","label":"系统提示词","tokens":5026697,"share":0.09264469911917171},{"key":"assistant_thinking","label":"模型思考","tokens":9901176,"share":0.18248393478933664},{"key":"assistant_tool_use","label":"工具调用","tokens":6298456,"share":0.116083891209155},{"key":"assistant_text","label":"模型回复","tokens":285174,"share":0.005255916477293408},{"key":"user_input","label":"用户输入","tokens":117467,"share":0.0021649835267982995},{"key":"attachment","label":"附件/技能","tokens":3462027,"share":0.0638069897611644}],"byCategoryDetail":[{"key":"tool_result","label":"工具返回","tokens":29166797,"share":0.5375595851170794,"children":[{"tool":"Read","label":"docs/02-开发记录及Bug总结.md","kind":"read","tokens":7161743,"share":0.1319947322522777,"catShare":0.24554437481294195,"trips":695},{"tool":"Bash","label":"（无路径）","kind":"shell","tokens":6203425,"share":0.11433242828302448,"catShare":0.21268791674158896,"trips":11658},{"tool":"Read","label":"frontend/apps/screen/src/charts/index.ts","kind":"read","tokens":2107823,"share":0.0388482907575804,"catShare":0.07226787845131499,"trips":874},{"tool":"Read","label":".workbuddy/memory/MEMORY.md","kind":"read","tokens":1550948,"share":0.028584793568585776,"catShare":0.05317511650798687,"trips":338},{"tool":"Read","label":"frontend/apps/screen/src/views/LineView.vue","kind":"read","tokens":1398630,"share":0.02577749254017602,"catShare":0.047952809797934745,"trips":232},{"tool":"Read","label":"README.md","kind":"read","tokens":1109328,"share":0.020445507451888764,"catShare":0.03803393710752227,"trips":245},{"tool":"Read","label":"backend/apps/energy/services/ranges.py","kind":"read","tokens":1097538,"share":0.020228212905130175,"catShare":0.037629712994001425,"trips":238},{"tool":"mcp_call","label":"（无路径）","kind":"other","tokens":1044210,"share":0.01924535168372332,"catShare":0.035801336663975065,"trips":6862},{"tool":"Read","label":"frontend/apps/admin/src/views/energy/UsageQueryView.vue","kind":"read","tokens":1020201,"share":0.018802847236583646,"catShare":0.034978163829947194,"trips":233},{"tool":"Read","label":"docs/reference/项目规范.md","kind":"read","tokens":1008328,"share":0.01858402784189741,"catShare":0.03457110310450486,"trips":245},{"tool":"Read","label":"frontend/apps/screen/src/views/OverviewView.vue","kind":"read","tokens":972545,"share":0.017924529622686647,"catShare":0.03334426567574406,"trips":241},{"tool":"Read","label":"backend/apps/energy/services/summary.py","kind":"read","tokens":860942,"share":0.015867610903142506,"catShare":0.029517864330679874,"trips":225},{"tool":"Read","label":"frontend/apps/admin/src/utils/charts.ts","kind":"read","tokens":545733,"share":0.01005815151442422,"catShare":0.01871076582558485,"trips":196},{"tool":"Read","label":"backend/apps/energy/services/snapshot.py","kind":"read","tokens":418819,"share":0.00771905577031985,"catShare":0.014359442160516319,"trips":226},{"tool":"Read","label":"frontend/apps/screen/src/stores/energy.ts","kind":"read","tokens":347114,"share":0.006397491144341238,"catShare":0.011900989809246686,"trips":231},{"tool":null,"label":"其他 34 项","kind":null,"tokens":2319470,"share":0.04274906164129978,"catShare":0.07952432218651467,"trips":13338}]},{"key":"system","label":"系统提示词","tokens":5026697,"share":0.09264469911917171,"children":[]},{"key":"assistant_thinking","label":"模型思考","tokens":9901176,"share":0.18248393478933664,"children":[]},{"key":"assistant_tool_use","label":"工具调用","tokens":6298456,"share":0.116083891209155,"children":[{"tool":"Edit","label":"frontend/apps/screen/src/charts/index.ts","kind":"write","tokens":1246028,"share":0.022964951127851296,"catShare":0.19783064548098261,"trips":2269},{"tool":"Bash","label":"（无路径）","kind":"shell","tokens":1178950,"share":0.021728676413873806,"catShare":0.18718080680741486,"trips":11658},{"tool":"mcp_call","label":"（无路径）","kind":"other","tokens":1074628,"share":0.01980595662052323,"catShare":0.17061761467693828,"trips":6862},{"tool":"Edit","label":"docs/02-开发记录及Bug总结.md","kind":"write","tokens":770232,"share":0.014195785476468506,"catShare":0.1222890215739852,"trips":1199},{"tool":"AskUserQuestion","label":"（无路径）","kind":"other","tokens":334239,"share":0.006160198997955713,"catShare":0.053066785871749685,"trips":450},{"tool":"Edit","label":"backend/apps/energy/tests.py","kind":"write","tokens":248675,"share":0.004583207344705194,"catShare":0.03948185486345704,"trips":277},{"tool":"Edit","label":"frontend/apps/screen/src/views/OverviewView.vue","kind":"write","tokens":224402,"share":0.004135845787851845,"catShare":0.03562807677079032,"trips":981},{"tool":"Edit","label":"frontend/apps/admin/src/utils/charts.ts","kind":"write","tokens":167890,"share":0.0030942945490091997,"catShare":0.02665567562198645,"trips":691},{"tool":"Edit","label":"backend/apps/energy/services/summary.py","kind":"write","tokens":148856,"share":0.0027434980316995796,"catShare":0.02363375316870161,"trips":194},{"tool":"Edit","label":"frontend/apps/screen/src/views/LineView.vue","kind":"write","tokens":141797,"share":0.0026134029863813584,"catShare":0.022513054646597266,"trips":487},{"tool":"TaskCreate","label":"（无路径）","kind":"other","tokens":131074,"share":0.0024157677562050336,"catShare":0.020810533925438513,"trips":1075},{"tool":"Edit","label":"frontend/packages/shared/src/types.ts","kind":"write","tokens":109286,"share":0.002014201096107549,"catShare":0.017351254124299192,"trips":384},{"tool":"Edit","label":"backend/docs/api-contract.md","kind":"write","tokens":73023,"share":0.001345848876696561,"catShare":0.011593760879980049,"trips":201},{"tool":"Edit","label":".workbuddy/memory/MEMORY.md","kind":"write","tokens":72170,"share":0.0013301228629419486,"catShare":0.011458289768606997,"trips":95},{"tool":"Edit","label":"frontend/apps/admin/src/views/energy/UsageQueryView.vue","kind":"write","tokens":47440,"share":0.0008743534340100054,"catShare":0.007532082400947714,"trips":178},{"tool":null,"label":"其他 34 项","kind":null,"tokens":329767,"share":0.006077779846873437,"catShare":0.052356789418117916,"trips":9076}]},{"key":"assistant_text","label":"模型回复","tokens":285174,"share":0.005255916477293408,"children":[]},{"key":"user_input","label":"用户输入","tokens":117467,"share":0.0021649835267982995,"children":[]},{"key":"attachment","label":"附件/技能","tokens":3462027,"share":0.0638069897611644,"children":[]}],"byTool":[{"tool":"Read","tokens":21348066,"calls":41,"perCall":520685,"share":0.3934562159809341},{"tool":"Bash","tokens":7382375,"calls":84,"perCall":87885,"share":0.13606110469689822},{"tool":"Edit","tokens":3719809,"calls":62,"perCall":59997,"share":0.06855806726968958},{"tool":"mcp_call","tokens":2118838,"calls":67,"perCall":31624,"share":0.03905130830424661},{"tool":"AskUserQuestion","tokens":451837,"calls":2,"perCall":225919,"share":0.008327600250401173},{"tool":"mcp_get","tokens":267716,"calls":6,"perCall":44619,"share":0.004934140566091024},{"tool":"TaskCreate","tokens":161511,"calls":5,"perCall":32302,"share":0.0029767325535260533},{"tool":"TaskUpdate","tokens":15100,"calls":6,"perCall":2517,"share":0.00027830670444809976}],"byFile":[{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\docs\\02-开发记录及Bug总结.md","label":"docs/02-开发记录及Bug总结.md","kind":"read","tool":"Read","selfTokens":38191,"reads":21,"trips":3788,"billed":8024844,"perRead":382135,"share":0.1479021471879181},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\apps\\screen\\src\\charts\\index.ts","label":"frontend/apps/screen/src/charts/index.ts","kind":"read","tool":"Read","selfTokens":17978,"reads":21,"trips":6286,"billed":3517050,"perRead":167479,"share":0.06482110445840342},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\.workbuddy\\memory\\MEMORY.md","label":".workbuddy/memory/MEMORY.md","kind":"read","tool":"Read","selfTokens":7270,"reads":4,"trips":866,"billed":1638726,"perRead":409681,"share":0.03020258516786786},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\apps\\screen\\src\\views\\LineView.vue","label":"frontend/apps/screen/src/views/LineView.vue","kind":"read","tool":"Read","selfTokens":6238,"reads":4,"trips":1438,"billed":1580618,"perRead":395154,"share":0.029131624397067155},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\apps\\screen\\src\\views\\OverviewView.vue","label":"frontend/apps/screen/src/views/OverviewView.vue","kind":"read","tool":"Read","selfTokens":5344,"reads":8,"trips":2444,"billed":1267639,"perRead":158455,"share":0.023363256411578916},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\README.md","label":"README.md","kind":"read","tool":"Read","selfTokens":4743,"reads":4,"trips":894,"billed":1160646,"perRead":290162,"share":0.02139132843486518},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\backend\\apps\\energy\\services\\ranges.py","label":"backend/apps/energy/services/ranges.py","kind":"read","tool":"Read","selfTokens":4081,"reads":1,"trips":476,"billed":1106212,"perRead":1106212,"share":0.02038808023359749},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\apps\\admin\\src\\views\\energy\\UsageQueryView.vue","label":"frontend/apps/admin/src/views/energy/UsageQueryView.vue","kind":"read","tool":"Read","selfTokens":4170,"reads":2,"trips":822,"billed":1089482,"perRead":544741,"share":0.020079729570869415},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\backend\\apps\\energy\\services\\summary.py","label":"backend/apps/energy/services/summary.py","kind":"read","tool":"Read","selfTokens":4114,"reads":2,"trips":838,"billed":1030467,"perRead":515234,"share":0.018992060684028703},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\docs\\reference\\项目规范.md","label":"docs/reference/项目规范.md","kind":"read","tool":"Read","selfTokens":3656,"reads":1,"trips":490,"billed":1017232,"perRead":1017232,"share":0.018748125217984805},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\apps\\admin\\src\\utils\\charts.ts","label":"frontend/apps/admin/src/utils/charts.ts","kind":"read","tool":"Read","selfTokens":3559,"reads":5,"trips":1774,"billed":764491,"perRead":152898,"share":0.014089976703029663},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\backend\\apps\\energy\\tests.py","label":"backend/apps/energy/tests.py","kind":"read","tool":"Read","selfTokens":3736,"reads":5,"trips":1398,"billed":596569,"perRead":119314,"share":0.010995092962159015},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\backend\\apps\\energy\\services\\snapshot.py","label":"backend/apps/energy/services/snapshot.py","kind":"read","tool":"Read","selfTokens":1664,"reads":1,"trips":452,"billed":428871,"perRead":428871,"share":0.007904313108807525},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\apps\\screen\\src\\stores\\energy.ts","label":"frontend/apps/screen/src/stores/energy.ts","kind":"read","tool":"Read","selfTokens":1351,"reads":1,"trips":462,"billed":355805,"perRead":355805,"share":0.006557671119882404},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\packages\\shared\\src\\mock\\energy.ts","label":"frontend/packages/shared/src/mock/energy.ts","kind":"read","tool":"Read","selfTokens":1263,"reads":1,"trips":470,"billed":338250,"perRead":338250,"share":0.006234124310778896},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\packages\\shared\\src\\format.ts","label":"frontend/packages/shared/src/format.ts","kind":"read","tool":"Read","selfTokens":1205,"reads":1,"trips":394,"billed":271430,"perRead":271430,"share":0.005002598134095156},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\apps\\screen\\src\\components\\DateSwitch.vue","label":"frontend/apps/screen/src/components/DateSwitch.vue","kind":"read","tool":"Read","selfTokens":763,"reads":1,"trips":482,"billed":209206,"perRead":209206,"share":0.003855769975221288},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\backend\\apps\\energy\\urls.py","label":"backend/apps/energy/urls.py","kind":"read","tool":"Read","selfTokens":784,"reads":1,"trips":458,"billed":204713,"perRead":204713,"share":0.003772969032460047},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\packages\\shared\\src\\types.ts","label":"frontend/packages/shared/src/types.ts","kind":"write","tool":"Edit","selfTokens":574,"reads":2,"trips":768,"billed":125973,"perRead":62987,"share":0.0023217494145419022},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\apps\\screen\\src\\composables\\useEcharts.ts","label":"frontend/apps/screen/src/composables/useEcharts.ts","kind":"read","tool":"Read","selfTokens":1235,"reads":1,"trips":124,"billed":88349,"perRead":88349,"share":0.0016283137224990972},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\backend\\docs\\api-contract.md","label":"backend/docs/api-contract.md","kind":"write","tool":"Edit","selfTokens":710,"reads":2,"trips":402,"billed":81296,"perRead":40648,"share":0.0014983313134399477},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\apps\\screen\\src\\components\\ChartBox.vue","label":"frontend/apps/screen/src/components/ChartBox.vue","kind":"read","tool":"Read","selfTokens":463,"reads":1,"trips":160,"billed":42468,"perRead":42468,"share":0.0007827163079910017},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\backend\\README.md","label":"backend/README.md","kind":"write","tool":"Edit","selfTokens":238,"reads":2,"trips":414,"billed":28177,"perRead":14089,"share":0.0005193250692304512},{"attr":"file:C:\\Users\\cat\\AppData\\Local\\Temp\\eep-shots\\before-line-A1.png","label":"C:/Users/cat/AppData/Local/Temp/eep-shots/before-line-A1.png","kind":"read","tool":"Read","selfTokens":104,"reads":1,"trips":404,"billed":24011,"perRead":24011,"share":0.00044253561074313356},{"attr":"file:C:\\Users\\cat\\AppData\\Local\\Temp\\eep-shots\\before-overview.png","label":"C:/Users/cat/AppData/Local/Temp/eep-shots/before-overview.png","kind":"read","tool":"Read","selfTokens":105,"reads":1,"trips":398,"billed":23888,"perRead":23888,"share":0.0004402701963945756},{"attr":"file:C:\\Users\\cat\\AppData\\Local\\Temp\\eep-shots\\after-line-A1-day.png","label":"C:/Users/cat/AppData/Local/Temp/eep-shots/after-line-A1-day.png","kind":"read","tool":"Read","selfTokens":106,"reads":1,"trips":338,"billed":20496,"perRead":20496,"share":0.0003777482930359702},{"attr":"file:E:\\能源产效管理平台\\electricity_vue_system\\frontend\\packages\\shared\\src\\config.ts","label":"frontend/packages/shared/src/config.ts","kind":"write","tool":"Edit","selfTokens":190,"reads":2,"trips":166,"billed":9143,"perRead":4572,"share":0.0001685178409265324},{"attr":"file:C:\\Users\\cat\\AppData\\Local\\Temp\\eep-shots\\trend-overview-now.png","label":"C:/Users/cat/AppData/Local/Temp/eep-shots/trend-overview-now.png","kind":"read","tool":"Read","selfTokens":106,"reads":1,"trips":84,"billed":5201,"perRead":5201,"share":0.00009586351728744571},{"attr":"file:C:\\Users\\cat\\AppData\\Local\\Temp\\eep-shots\\trend-full.png","label":"C:/Users/cat/AppData/Local/Temp/eep-shots/trend-full.png","kind":"read","tool":"Read","selfTokens":104,"reads":1,"trips":78,"billed":4750,"perRead":4750,"share":0.00008753591686699364},{"attr":"file:C:\\Users\\cat\\AppData\\Local\\Temp\\eep-shots\\overviewDay.png","label":"C:/Users/cat/AppData/Local/Temp/eep-shots/overviewDay.png","kind":"read","tool":"Read","selfTokens":103,"reads":1,"trips":62,"billed":3744,"perRead":3744,"share":0.00006900023634927088},{"attr":"file:C:\\Users\\cat\\AppData\\Local\\Temp\\eep-shots\\compare.png","label":"C:/Users/cat/AppData/Local/Temp/eep-shots/compare.png","kind":"read","tool":"Read","selfTokens":102,"reads":1,"trips":54,"billed":3228,"perRead":3228,"share":0.00005949527098869484},{"attr":"file:C:\\Users\\cat\\AppData\\Local\\Temp\\eep-shots\\line-trend-live.png","label":"C:/Users/cat/AppData/Local/Temp/eep-shots/line-trend-live.png","kind":"read","tool":"Read","selfTokens":105,"reads":1,"trips":48,"billed":2953,"perRead":2953,"share":0.00005442641548662188},{"attr":"file:C:\\Users\\cat\\AppData\\Local\\Temp\\eep-shots\\compare-real.png","label":"C:/Users/cat/AppData/Local/Temp/eep-shots/compare-real.png","kind":"read","tool":"Read","selfTokens":104,"reads":1,"trips":32,"billed":1948,"perRead":1948,"share":0.000035897014227440134}],"byAttr":[{"attr":"tool_result|Read","tokens":21104282,"share":0.38896314853588126},{"attr":"assistant_thinking","tokens":9901176,"share":0.18248393478933664},{"attr":"tool_result|Bash","tokens":6203425,"share":0.11433242828302448},{"attr":"system","tokens":5026697,"share":0.09264469911917171},{"attr":"attachment","tokens":3462027,"share":0.0638069897611644},{"attr":"tool_use|Edit","tokens":3309078,"share":0.060988063466903886},{"attr":"tool_use|Bash","tokens":1178950,"share":0.021728676413873806},{"attr":"tool_use|mcp_call","tokens":1074628,"share":0.01980595662052323},{"attr":"tool_result|mcp_call","tokens":1044210,"share":0.01924535168372332},{"attr":"tool_result|Edit","tokens":410732,"share":0.007570003802785451},{"attr":"tool_use|AskUserQuestion","tokens":334239,"share":0.006160198997955713},{"attr":"assistant_text","tokens":285174,"share":0.005255916477293408},{"attr":"tool_result|mcp_get","tokens":250072,"share":0.004608964080122209},{"attr":"tool_use|Read","tokens":243784,"share":0.004493067445054772},{"attr":"tool_use|TaskCreate","tokens":131074,"share":0.0024157677562050336},{"attr":"tool_result|AskUserQuestion","tokens":117598,"share":0.0021674012524454615},{"attr":"user_input","tokens":117467,"share":0.0021649835267982995},{"attr":"tool_result|TaskCreate","tokens":30437,"share":0.000560964797321019},{"attr":"tool_use|mcp_get","tokens":17643,"share":0.00032517648596881553},{"attr":"tool_use|TaskUpdate","tokens":9060,"share":0.00016698402266885948},{"attr":"tool_result|TaskUpdate","tokens":6040,"share":0.0001113226817792399}],"byRequest":[{"index":1,"time":"14:04","ratio":0.023123,"inputTokens":23123,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.653,"originalCredits":0.653,"afterCompact":false},{"index":2,"time":"14:04","ratio":0.024697,"inputTokens":24697,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.103,"originalCredits":0.103,"afterCompact":false},{"index":3,"time":"14:04","ratio":0.032595,"inputTokens":32595,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.284,"originalCredits":0.284,"afterCompact":false},{"index":4,"time":"14:04","ratio":0.038434,"inputTokens":38434,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.239,"originalCredits":0.239,"afterCompact":false},{"index":5,"time":"14:04","ratio":0.060981,"inputTokens":60981,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.745,"originalCredits":0.745,"afterCompact":false},{"index":6,"time":"14:05","ratio":0.062384,"inputTokens":62384,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.196,"originalCredits":0.196,"afterCompact":false},{"index":7,"time":"14:05","ratio":0.068015,"inputTokens":68015,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.3,"originalCredits":0.3,"afterCompact":false},{"index":8,"time":"14:05","ratio":0.077319,"inputTokens":77319,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.433,"originalCredits":0.433,"afterCompact":false},{"index":9,"time":"14:05","ratio":0.079416,"inputTokens":79416,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.239,"originalCredits":0.239,"afterCompact":false},{"index":10,"time":"14:05","ratio":0.085916,"inputTokens":85916,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.365,"originalCredits":0.365,"afterCompact":false},{"index":11,"time":"14:05","ratio":0.086485,"inputTokens":86485,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.217,"originalCredits":0.217,"afterCompact":false},{"index":12,"time":"14:05","ratio":0.087322,"inputTokens":87322,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.243,"originalCredits":0.243,"afterCompact":false},{"index":13,"time":"14:06","ratio":0.090283,"inputTokens":90283,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.325,"originalCredits":0.325,"afterCompact":false},{"index":14,"time":"14:06","ratio":0.093744,"inputTokens":93744,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.325,"originalCredits":0.325,"afterCompact":false},{"index":15,"time":"14:06","ratio":0.099117,"inputTokens":99117,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.384,"originalCredits":0.384,"afterCompact":false},{"index":16,"time":"14:06","ratio":0.106388,"inputTokens":106388,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.456,"originalCredits":0.456,"afterCompact":false},{"index":17,"time":"14:07","ratio":0.109943,"inputTokens":109943,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.349,"originalCredits":0.349,"afterCompact":false},{"index":18,"time":"14:07","ratio":0.112082,"inputTokens":112082,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.313,"originalCredits":0.313,"afterCompact":false},{"index":19,"time":"14:07","ratio":0.11432,"inputTokens":114320,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.414,"originalCredits":0.414,"afterCompact":false},{"index":20,"time":"14:08","ratio":0.116835,"inputTokens":116835,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.387,"originalCredits":0.387,"afterCompact":false},{"index":21,"time":"14:10","ratio":0.118056,"inputTokens":118056,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.353,"originalCredits":0.353,"afterCompact":false},{"index":22,"time":"14:10","ratio":0.121006,"inputTokens":121006,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.376,"originalCredits":0.376,"afterCompact":false},{"index":23,"time":"14:11","ratio":0.125504,"inputTokens":125504,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.669,"originalCredits":0.669,"afterCompact":false},{"index":24,"time":"14:12","ratio":0.131132,"inputTokens":131132,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.469,"originalCredits":0.469,"afterCompact":false},{"index":25,"time":"14:18","ratio":0.131858,"inputTokens":131858,"outputTokens":null,"usageSource":"transcript-ratio","credits":3.709,"originalCredits":3.709,"afterCompact":false},{"index":26,"time":"14:18","ratio":0.133947,"inputTokens":133947,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.373,"originalCredits":0.373,"afterCompact":false},{"index":27,"time":"14:19","ratio":0.13442,"inputTokens":134420,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.327,"originalCredits":0.327,"afterCompact":false},{"index":28,"time":"14:19","ratio":0.136267,"inputTokens":136267,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.376,"originalCredits":0.376,"afterCompact":false},{"index":29,"time":"14:19","ratio":0.138178,"inputTokens":138178,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.421,"originalCredits":0.421,"afterCompact":false},{"index":30,"time":"14:19","ratio":0.139739,"inputTokens":139739,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.365,"originalCredits":0.365,"afterCompact":false},{"index":31,"time":"14:20","ratio":0.141743,"inputTokens":141743,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.379,"originalCredits":0.379,"afterCompact":false},{"index":32,"time":"14:21","ratio":0.143531,"inputTokens":143531,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.412,"originalCredits":0.412,"afterCompact":false},{"index":33,"time":"14:21","ratio":0.144449,"inputTokens":144449,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.356,"originalCredits":0.356,"afterCompact":false},{"index":34,"time":"14:21","ratio":0.144952,"inputTokens":144952,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.343,"originalCredits":0.343,"afterCompact":false},{"index":35,"time":"14:21","ratio":0.145138,"inputTokens":145138,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.335,"originalCredits":0.335,"afterCompact":false},{"index":36,"time":"14:22","ratio":0.145615,"inputTokens":145615,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.344,"originalCredits":0.344,"afterCompact":false},{"index":37,"time":"14:22","ratio":0.145792,"inputTokens":145792,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.333,"originalCredits":0.333,"afterCompact":false},{"index":38,"time":"14:22","ratio":0.145887,"inputTokens":145887,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.339,"originalCredits":0.339,"afterCompact":false},{"index":39,"time":"14:22","ratio":0.146434,"inputTokens":146434,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.354,"originalCredits":0.354,"afterCompact":false},{"index":40,"time":"14:22","ratio":0.147285,"inputTokens":147285,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.376,"originalCredits":0.376,"afterCompact":false},{"index":41,"time":"14:22","ratio":0.147762,"inputTokens":147762,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.365,"originalCredits":0.365,"afterCompact":false},{"index":42,"time":"14:22","ratio":0.148494,"inputTokens":148494,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.374,"originalCredits":0.374,"afterCompact":false},{"index":43,"time":"14:23","ratio":0.148949,"inputTokens":148949,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.347,"originalCredits":0.347,"afterCompact":false},{"index":44,"time":"14:23","ratio":0.149044,"inputTokens":149044,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.36,"originalCredits":0.36,"afterCompact":false},{"index":45,"time":"14:23","ratio":0.149517,"inputTokens":149517,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.348,"originalCredits":0.348,"afterCompact":false},{"index":46,"time":"14:23","ratio":0.149882,"inputTokens":149882,"outputTokens":null,"usageSource":"transcript-ratio","credits":3.652,"originalCredits":3.652,"afterCompact":false},{"index":47,"time":"14:23","ratio":0.150111,"inputTokens":150111,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.351,"originalCredits":0.351,"afterCompact":false},{"index":48,"time":"14:24","ratio":0.150334,"inputTokens":150334,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.344,"originalCredits":0.344,"afterCompact":false},{"index":49,"time":"14:24","ratio":0.150708,"inputTokens":150708,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.461,"originalCredits":0.461,"afterCompact":false},{"index":50,"time":"14:25","ratio":0.155043,"inputTokens":155043,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.553,"originalCredits":0.553,"afterCompact":false},{"index":51,"time":"14:25","ratio":0.160914,"inputTokens":160914,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.531,"originalCredits":0.531,"afterCompact":false},{"index":52,"time":"14:25","ratio":0.164838,"inputTokens":164838,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.479,"originalCredits":0.479,"afterCompact":false},{"index":53,"time":"14:25","ratio":0.165565,"inputTokens":165565,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.46,"originalCredits":0.46,"afterCompact":false},{"index":54,"time":"14:25","ratio":0.166851,"inputTokens":166851,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.427,"originalCredits":0.427,"afterCompact":false},{"index":55,"time":"14:26","ratio":0.167282,"inputTokens":167282,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.394,"originalCredits":0.394,"afterCompact":false},{"index":56,"time":"14:26","ratio":0.16802,"inputTokens":168020,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.418,"originalCredits":0.418,"afterCompact":false},{"index":57,"time":"14:26","ratio":0.16849,"inputTokens":168490,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.445,"originalCredits":0.445,"afterCompact":false},{"index":58,"time":"14:26","ratio":0.169728,"inputTokens":169728,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.418,"originalCredits":0.418,"afterCompact":false},{"index":59,"time":"14:26","ratio":0.169917,"inputTokens":169917,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.437,"originalCredits":0.437,"afterCompact":false},{"index":60,"time":"14:27","ratio":0.170894,"inputTokens":170894,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.481,"originalCredits":0.481,"afterCompact":false},{"index":61,"time":"14:27","ratio":0.172229,"inputTokens":172229,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.434,"originalCredits":0.434,"afterCompact":false},{"index":62,"time":"14:27","ratio":0.172578,"inputTokens":172578,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.406,"originalCredits":0.406,"afterCompact":false},{"index":63,"time":"14:27","ratio":0.172871,"inputTokens":172871,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.443,"originalCredits":0.443,"afterCompact":false},{"index":64,"time":"14:27","ratio":0.173781,"inputTokens":173781,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.424,"originalCredits":0.424,"afterCompact":false},{"index":65,"time":"14:28","ratio":0.174087,"inputTokens":174087,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.425,"originalCredits":0.425,"afterCompact":false},{"index":66,"time":"14:28","ratio":0.174654,"inputTokens":174654,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.423,"originalCredits":0.423,"afterCompact":false},{"index":67,"time":"14:28","ratio":0.175048,"inputTokens":175048,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.414,"originalCredits":0.414,"afterCompact":false},{"index":68,"time":"14:28","ratio":0.175355,"inputTokens":175355,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.412,"originalCredits":0.412,"afterCompact":false},{"index":69,"time":"14:28","ratio":0.175861,"inputTokens":175861,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.434,"originalCredits":0.434,"afterCompact":false},{"index":70,"time":"14:29","ratio":0.176426,"inputTokens":176426,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.417,"originalCredits":0.417,"afterCompact":false},{"index":71,"time":"14:29","ratio":0.176677,"inputTokens":176677,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.419,"originalCredits":0.419,"afterCompact":false},{"index":72,"time":"14:29","ratio":0.177485,"inputTokens":177485,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.434,"originalCredits":0.434,"afterCompact":false},{"index":73,"time":"14:29","ratio":0.177827,"inputTokens":177827,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.414,"originalCredits":0.414,"afterCompact":false},{"index":74,"time":"14:30","ratio":0.178232,"inputTokens":178232,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.427,"originalCredits":0.427,"afterCompact":false},{"index":75,"time":"14:30","ratio":0.178753,"inputTokens":178753,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.428,"originalCredits":0.428,"afterCompact":false},{"index":76,"time":"14:30","ratio":0.179085,"inputTokens":179085,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.42,"originalCredits":0.42,"afterCompact":false},{"index":77,"time":"14:30","ratio":0.179579,"inputTokens":179579,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.418,"originalCredits":0.418,"afterCompact":false},{"index":78,"time":"14:30","ratio":0.179722,"inputTokens":179722,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.408,"originalCredits":0.408,"afterCompact":false},{"index":79,"time":"14:31","ratio":0.180115,"inputTokens":180115,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.434,"originalCredits":0.434,"afterCompact":false},{"index":80,"time":"14:31","ratio":0.180556,"inputTokens":180556,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.454,"originalCredits":0.454,"afterCompact":false},{"index":81,"time":"14:31","ratio":0.181742,"inputTokens":181742,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.52,"originalCredits":0.52,"afterCompact":false},{"index":82,"time":"14:33","ratio":0.183562,"inputTokens":183562,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.809,"originalCredits":0.809,"afterCompact":false},{"index":83,"time":"14:34","ratio":0.189527,"inputTokens":189527,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.641,"originalCredits":0.641,"afterCompact":false},{"index":84,"time":"14:34","ratio":0.190711,"inputTokens":190711,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.46,"originalCredits":0.46,"afterCompact":false},{"index":85,"time":"14:34","ratio":0.191942,"inputTokens":191942,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.501,"originalCredits":0.501,"afterCompact":false},{"index":86,"time":"14:34","ratio":0.192711,"inputTokens":192711,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.453,"originalCredits":0.453,"afterCompact":false},{"index":87,"time":"14:34","ratio":0.193846,"inputTokens":193846,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.471,"originalCredits":0.471,"afterCompact":false},{"index":88,"time":"14:35","ratio":0.194328,"inputTokens":194328,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.5,"originalCredits":0.5,"afterCompact":false},{"index":89,"time":"14:35","ratio":0.195302,"inputTokens":195302,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.476,"originalCredits":0.476,"afterCompact":false},{"index":90,"time":"14:35","ratio":0.195641,"inputTokens":195641,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.454,"originalCredits":0.454,"afterCompact":false},{"index":91,"time":"14:35","ratio":0.195865,"inputTokens":195865,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.463,"originalCredits":0.463,"afterCompact":false},{"index":92,"time":"14:36","ratio":0.196297,"inputTokens":196297,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.458,"originalCredits":0.458,"afterCompact":false},{"index":93,"time":"14:36","ratio":0.196544,"inputTokens":196544,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.456,"originalCredits":0.456,"afterCompact":false},{"index":94,"time":"14:36","ratio":0.19682,"inputTokens":196820,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.479,"originalCredits":0.479,"afterCompact":false},{"index":95,"time":"14:36","ratio":0.197668,"inputTokens":197668,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.47,"originalCredits":0.47,"afterCompact":false},{"index":96,"time":"14:36","ratio":0.197884,"inputTokens":197884,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.473,"originalCredits":0.473,"afterCompact":false},{"index":97,"time":"14:36","ratio":0.198407,"inputTokens":198407,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.466,"originalCredits":0.466,"afterCompact":false},{"index":98,"time":"14:37","ratio":0.198833,"inputTokens":198833,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.463,"originalCredits":0.463,"afterCompact":false},{"index":99,"time":"14:37","ratio":0.199058,"inputTokens":199058,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.459,"originalCredits":0.459,"afterCompact":false},{"index":100,"time":"14:37","ratio":0.199304,"inputTokens":199304,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.477,"originalCredits":0.477,"afterCompact":false},{"index":101,"time":"14:38","ratio":0.200387,"inputTokens":200387,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.497,"originalCredits":0.497,"afterCompact":false},{"index":102,"time":"14:38","ratio":0.200811,"inputTokens":200811,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.486,"originalCredits":0.486,"afterCompact":false},{"index":103,"time":"14:38","ratio":0.20137,"inputTokens":201370,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.475,"originalCredits":0.475,"afterCompact":false},{"index":104,"time":"14:38","ratio":0.20183,"inputTokens":201830,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.484,"originalCredits":0.484,"afterCompact":false},{"index":105,"time":"14:39","ratio":0.202551,"inputTokens":202551,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.488,"originalCredits":0.488,"afterCompact":false},{"index":106,"time":"14:39","ratio":0.203641,"inputTokens":203641,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.492,"originalCredits":0.492,"afterCompact":false},{"index":107,"time":"14:40","ratio":0.205541,"inputTokens":205541,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.52,"originalCredits":0.52,"afterCompact":false},{"index":108,"time":"14:41","ratio":0.206479,"inputTokens":206479,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.584,"originalCredits":0.584,"afterCompact":false},{"index":109,"time":"14:41","ratio":0.208206,"inputTokens":208206,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.596,"originalCredits":0.596,"afterCompact":false},{"index":110,"time":"14:42","ratio":0.209742,"inputTokens":209742,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.528,"originalCredits":0.528,"afterCompact":false},{"index":111,"time":"14:42","ratio":0.212033,"inputTokens":212033,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.558,"originalCredits":0.558,"afterCompact":false},{"index":112,"time":"14:42","ratio":0.212624,"inputTokens":212624,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.501,"originalCredits":0.501,"afterCompact":false},{"index":113,"time":"14:43","ratio":0.213395,"inputTokens":213395,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.511,"originalCredits":0.511,"afterCompact":false},{"index":114,"time":"14:43","ratio":0.213729,"inputTokens":213729,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.491,"originalCredits":0.491,"afterCompact":false},{"index":115,"time":"14:44","ratio":0.213893,"inputTokens":213893,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.493,"originalCredits":0.493,"afterCompact":false},{"index":116,"time":"14:44","ratio":0.214223,"inputTokens":214223,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.497,"originalCredits":0.497,"afterCompact":false},{"index":117,"time":"14:44","ratio":0.214452,"inputTokens":214452,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.49,"originalCredits":0.49,"afterCompact":false},{"index":118,"time":"14:44","ratio":0.214627,"inputTokens":214627,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.489,"originalCredits":0.489,"afterCompact":false},{"index":119,"time":"14:44","ratio":0.214861,"inputTokens":214861,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.495,"originalCredits":0.495,"afterCompact":false},{"index":120,"time":"14:44","ratio":0.215085,"inputTokens":215085,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.512,"originalCredits":0.512,"afterCompact":false},{"index":121,"time":"14:45","ratio":0.215903,"inputTokens":215903,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.525,"originalCredits":0.525,"afterCompact":false},{"index":122,"time":"14:45","ratio":0.216352,"inputTokens":216352,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.5,"originalCredits":0.5,"afterCompact":false},{"index":123,"time":"14:45","ratio":0.216516,"inputTokens":216516,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.5,"originalCredits":0.5,"afterCompact":false},{"index":124,"time":"14:46","ratio":0.216945,"inputTokens":216945,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.586,"originalCredits":0.586,"afterCompact":false},{"index":125,"time":"14:46","ratio":0.218617,"inputTokens":218617,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.559,"originalCredits":0.559,"afterCompact":false},{"index":126,"time":"14:46","ratio":0.219306,"inputTokens":219306,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.515,"originalCredits":0.515,"afterCompact":false},{"index":127,"time":"14:47","ratio":0.219756,"inputTokens":219756,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.621,"originalCredits":0.621,"afterCompact":false},{"index":128,"time":"14:47","ratio":0.221849,"inputTokens":221849,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.56,"originalCredits":0.56,"afterCompact":false},{"index":129,"time":"14:47","ratio":0.222123,"inputTokens":222123,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.508,"originalCredits":0.508,"afterCompact":false},{"index":130,"time":"14:47","ratio":0.224594,"inputTokens":224594,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.578,"originalCredits":0.578,"afterCompact":false},{"index":131,"time":"14:48","ratio":0.2249,"inputTokens":224900,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.521,"originalCredits":0.521,"afterCompact":false},{"index":132,"time":"14:48","ratio":0.225161,"inputTokens":225161,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.583,"originalCredits":0.583,"afterCompact":false},{"index":133,"time":"14:48","ratio":0.226478,"inputTokens":226478,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.544,"originalCredits":0.544,"afterCompact":false},{"index":134,"time":"14:48","ratio":0.227328,"inputTokens":227328,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.582,"originalCredits":0.582,"afterCompact":false},{"index":135,"time":"14:49","ratio":0.228287,"inputTokens":228287,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.57,"originalCredits":0.57,"afterCompact":false},{"index":136,"time":"14:49","ratio":0.228972,"inputTokens":228972,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.534,"originalCredits":0.534,"afterCompact":false},{"index":137,"time":"14:49","ratio":0.229855,"inputTokens":229855,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.557,"originalCredits":0.557,"afterCompact":false},{"index":138,"time":"14:49","ratio":0.230287,"inputTokens":230287,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.533,"originalCredits":0.533,"afterCompact":false},{"index":139,"time":"14:49","ratio":0.230879,"inputTokens":230879,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.541,"originalCredits":0.541,"afterCompact":false},{"index":140,"time":"14:49","ratio":0.23134,"inputTokens":231340,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.534,"originalCredits":0.534,"afterCompact":false},{"index":141,"time":"14:50","ratio":0.23274,"inputTokens":232740,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.563,"originalCredits":0.563,"afterCompact":false},{"index":142,"time":"14:50","ratio":0.232956,"inputTokens":232956,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.53,"originalCredits":0.53,"afterCompact":false},{"index":143,"time":"14:50","ratio":0.233243,"inputTokens":233243,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.54,"originalCredits":0.54,"afterCompact":false},{"index":144,"time":"14:50","ratio":0.23352,"inputTokens":233520,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.533,"originalCredits":0.533,"afterCompact":false},{"index":145,"time":"14:50","ratio":0.233681,"inputTokens":233681,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.535,"originalCredits":0.535,"afterCompact":false},{"index":146,"time":"14:50","ratio":0.234596,"inputTokens":234596,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.556,"originalCredits":0.556,"afterCompact":false},{"index":147,"time":"14:51","ratio":0.234825,"inputTokens":234825,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.583,"originalCredits":0.583,"afterCompact":false},{"index":148,"time":"14:51","ratio":0.235783,"inputTokens":235783,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.563,"originalCredits":0.563,"afterCompact":false},{"index":149,"time":"14:51","ratio":0.236086,"inputTokens":236086,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.552,"originalCredits":0.552,"afterCompact":false},{"index":150,"time":"14:51","ratio":0.236666,"inputTokens":236666,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.549,"originalCredits":0.549,"afterCompact":false},{"index":151,"time":"14:51","ratio":0.236885,"inputTokens":236885,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.545,"originalCredits":0.545,"afterCompact":false},{"index":152,"time":"14:51","ratio":0.237115,"inputTokens":237115,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.551,"originalCredits":0.551,"afterCompact":false},{"index":153,"time":"14:52","ratio":0.237456,"inputTokens":237456,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.552,"originalCredits":0.552,"afterCompact":false},{"index":154,"time":"14:52","ratio":0.238129,"inputTokens":238129,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.591,"originalCredits":0.591,"afterCompact":false},{"index":155,"time":"14:52","ratio":0.238917,"inputTokens":238917,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.564,"originalCredits":0.564,"afterCompact":false},{"index":156,"time":"14:53","ratio":0.239778,"inputTokens":239778,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.639,"originalCredits":0.639,"afterCompact":false},{"index":157,"time":"14:53","ratio":0.241345,"inputTokens":241345,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.599,"originalCredits":0.599,"afterCompact":false},{"index":158,"time":"14:53","ratio":0.241764,"inputTokens":241764,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.568,"originalCredits":0.568,"afterCompact":false},{"index":159,"time":"14:53","ratio":0.242564,"inputTokens":242564,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.581,"originalCredits":0.581,"afterCompact":false},{"index":160,"time":"14:54","ratio":0.242955,"inputTokens":242955,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.57,"originalCredits":0.57,"afterCompact":false},{"index":161,"time":"14:54","ratio":0.24336,"inputTokens":243360,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.57,"originalCredits":0.57,"afterCompact":false},{"index":162,"time":"14:54","ratio":0.243744,"inputTokens":243744,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.559,"originalCredits":0.559,"afterCompact":false},{"index":163,"time":"14:54","ratio":0.244705,"inputTokens":244705,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.584,"originalCredits":0.584,"afterCompact":false},{"index":164,"time":"14:55","ratio":0.247391,"inputTokens":247391,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.654,"originalCredits":0.654,"afterCompact":false},{"index":165,"time":"14:55","ratio":0.248128,"inputTokens":248128,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.628,"originalCredits":0.628,"afterCompact":false},{"index":166,"time":"14:55","ratio":0.249114,"inputTokens":249114,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.645,"originalCredits":0.645,"afterCompact":false},{"index":167,"time":"15:05","ratio":0.251718,"inputTokens":251718,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.723,"originalCredits":0.723,"afterCompact":false},{"index":168,"time":"15:06","ratio":0.254481,"inputTokens":254481,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.643,"originalCredits":0.643,"afterCompact":false},{"index":169,"time":"15:06","ratio":0.254634,"inputTokens":254634,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.601,"originalCredits":0.601,"afterCompact":false},{"index":170,"time":"15:07","ratio":0.255617,"inputTokens":255617,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.797,"originalCredits":0.797,"afterCompact":false},{"index":171,"time":"15:08","ratio":0.260233,"inputTokens":260233,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.897,"originalCredits":0.897,"afterCompact":false},{"index":172,"time":"15:09","ratio":0.263628,"inputTokens":263628,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.706,"originalCredits":0.706,"afterCompact":false},{"index":173,"time":"15:09","ratio":0.264438,"inputTokens":264438,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.624,"originalCredits":0.624,"afterCompact":false},{"index":174,"time":"15:09","ratio":0.264745,"inputTokens":264745,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.603,"originalCredits":0.603,"afterCompact":false},{"index":175,"time":"15:09","ratio":0.265911,"inputTokens":265911,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.656,"originalCredits":0.656,"afterCompact":false},{"index":176,"time":"15:09","ratio":0.266549,"inputTokens":266549,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.639,"originalCredits":0.639,"afterCompact":false},{"index":177,"time":"15:10","ratio":0.267102,"inputTokens":267102,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.621,"originalCredits":0.621,"afterCompact":false},{"index":178,"time":"15:10","ratio":0.267518,"inputTokens":267518,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.615,"originalCredits":0.615,"afterCompact":false},{"index":179,"time":"15:11","ratio":0.267708,"inputTokens":267708,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.623,"originalCredits":0.623,"afterCompact":false},{"index":180,"time":"15:11","ratio":0.268261,"inputTokens":268261,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.659,"originalCredits":0.659,"afterCompact":false},{"index":181,"time":"15:11","ratio":0.269261,"inputTokens":269261,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.679,"originalCredits":0.679,"afterCompact":false},{"index":182,"time":"15:12","ratio":0.270241,"inputTokens":270241,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.661,"originalCredits":0.661,"afterCompact":false},{"index":183,"time":"15:12","ratio":0.271187,"inputTokens":271187,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.681,"originalCredits":0.681,"afterCompact":false},{"index":184,"time":"15:13","ratio":0.272129,"inputTokens":272129,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.691,"originalCredits":0.691,"afterCompact":false},{"index":185,"time":"15:14","ratio":0.273218,"inputTokens":273218,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.719,"originalCredits":0.719,"afterCompact":false},{"index":186,"time":"15:14","ratio":0.275917,"inputTokens":275917,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.756,"originalCredits":0.756,"afterCompact":false},{"index":187,"time":"15:15","ratio":0.277537,"inputTokens":277537,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.669,"originalCredits":0.669,"afterCompact":false},{"index":188,"time":"15:15","ratio":0.277766,"inputTokens":277766,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.633,"originalCredits":0.633,"afterCompact":false},{"index":189,"time":"15:15","ratio":0.277969,"inputTokens":277969,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.657,"originalCredits":0.657,"afterCompact":false},{"index":190,"time":"15:16","ratio":0.278657,"inputTokens":278657,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.696,"originalCredits":0.696,"afterCompact":false},{"index":191,"time":"15:17","ratio":0.279822,"inputTokens":279822,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.7,"originalCredits":0.7,"afterCompact":false},{"index":192,"time":"15:17","ratio":0.280928,"inputTokens":280928,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.69,"originalCredits":0.69,"afterCompact":false},{"index":193,"time":"15:18","ratio":0.281603,"inputTokens":281603,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.687,"originalCredits":0.687,"afterCompact":false},{"index":194,"time":"15:18","ratio":0.282398,"inputTokens":282398,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.669,"originalCredits":0.669,"afterCompact":false},{"index":195,"time":"15:19","ratio":0.282877,"inputTokens":282877,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.694,"originalCredits":0.694,"afterCompact":false},{"index":196,"time":"15:19","ratio":0.283908,"inputTokens":283908,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.709,"originalCredits":0.709,"afterCompact":false},{"index":197,"time":"15:20","ratio":0.285078,"inputTokens":285078,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.749,"originalCredits":0.749,"afterCompact":false},{"index":198,"time":"15:20","ratio":0.286541,"inputTokens":286541,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.752,"originalCredits":0.752,"afterCompact":false},{"index":199,"time":"15:21","ratio":0.287946,"inputTokens":287946,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.724,"originalCredits":0.724,"afterCompact":false},{"index":200,"time":"15:22","ratio":0.288825,"inputTokens":288825,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.702,"originalCredits":0.702,"afterCompact":false},{"index":201,"time":"15:22","ratio":0.289736,"inputTokens":289736,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.747,"originalCredits":0.747,"afterCompact":false},{"index":202,"time":"15:23","ratio":0.295974,"inputTokens":295974,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.826,"originalCredits":0.826,"afterCompact":false},{"index":203,"time":"15:24","ratio":0.29616,"inputTokens":296160,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.693,"originalCredits":0.693,"afterCompact":false},{"index":204,"time":"15:24","ratio":0.296708,"inputTokens":296708,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.684,"originalCredits":0.684,"afterCompact":false},{"index":205,"time":"15:25","ratio":0.296914,"inputTokens":296914,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.668,"originalCredits":0.668,"afterCompact":false},{"index":206,"time":"15:25","ratio":0.297076,"inputTokens":297076,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.754,"originalCredits":0.754,"afterCompact":false},{"index":207,"time":"15:26","ratio":0.298609,"inputTokens":298609,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.712,"originalCredits":0.712,"afterCompact":false},{"index":208,"time":"15:26","ratio":0.298798,"inputTokens":298798,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.672,"originalCredits":0.672,"afterCompact":false},{"index":209,"time":"15:26","ratio":0.299229,"inputTokens":299229,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.748,"originalCredits":0.748,"afterCompact":false},{"index":210,"time":"15:27","ratio":0.300752,"inputTokens":300752,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.775,"originalCredits":0.775,"afterCompact":false},{"index":211,"time":"15:27","ratio":0.319482,"inputTokens":319482,"outputTokens":null,"usageSource":"transcript-ratio","credits":1.199,"originalCredits":1.199,"afterCompact":false},{"index":212,"time":"15:28","ratio":0.319702,"inputTokens":319702,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.796,"originalCredits":0.796,"afterCompact":false},{"index":213,"time":"15:28","ratio":0.321069,"inputTokens":321069,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.759,"originalCredits":0.759,"afterCompact":false},{"index":214,"time":"15:29","ratio":0.321282,"inputTokens":321282,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.836,"originalCredits":0.836,"afterCompact":false},{"index":215,"time":"15:29","ratio":0.323258,"inputTokens":323258,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.805,"originalCredits":0.805,"afterCompact":false},{"index":216,"time":"15:30","ratio":0.323908,"inputTokens":323908,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.741,"originalCredits":0.741,"afterCompact":false},{"index":217,"time":"15:31","ratio":0.324357,"inputTokens":324357,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.773,"originalCredits":0.773,"afterCompact":false},{"index":218,"time":"15:31","ratio":0.325074,"inputTokens":325074,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.751,"originalCredits":0.751,"afterCompact":false},{"index":219,"time":"15:31","ratio":0.325698,"inputTokens":325698,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.749,"originalCredits":0.749,"afterCompact":false},{"index":220,"time":"15:35","ratio":0.32589,"inputTokens":325890,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.727,"originalCredits":0.727,"afterCompact":false},{"index":221,"time":"15:37","ratio":0.32614,"inputTokens":326140,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.834,"originalCredits":0.834,"afterCompact":false},{"index":222,"time":"15:41","ratio":0.327895,"inputTokens":327895,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.739,"originalCredits":0.739,"afterCompact":false},{"index":223,"time":"15:42","ratio":0.328927,"inputTokens":328927,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.763,"originalCredits":0.763,"afterCompact":false},{"index":224,"time":"15:47","ratio":0.329403,"inputTokens":329403,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.744,"originalCredits":0.744,"afterCompact":false},{"index":225,"time":"15:50","ratio":0.329649,"inputTokens":329649,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.742,"originalCredits":0.742,"afterCompact":false},{"index":226,"time":"15:50","ratio":0.329843,"inputTokens":329843,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.746,"originalCredits":0.746,"afterCompact":false},{"index":227,"time":"15:51","ratio":0.330018,"inputTokens":330018,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.768,"originalCredits":0.768,"afterCompact":false},{"index":228,"time":"15:56","ratio":0.330767,"inputTokens":330767,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.744,"originalCredits":0.744,"afterCompact":false},{"index":229,"time":"16:05","ratio":0.330989,"inputTokens":330989,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.754,"originalCredits":0.754,"afterCompact":false},{"index":230,"time":"16:10","ratio":0.331343,"inputTokens":331343,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.744,"originalCredits":0.744,"afterCompact":false},{"index":231,"time":"16:13","ratio":0.331511,"inputTokens":331511,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.744,"originalCredits":0.744,"afterCompact":false},{"index":232,"time":"16:29","ratio":0.331763,"inputTokens":331763,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.751,"originalCredits":0.751,"afterCompact":false},{"index":233,"time":"16:33","ratio":0.332083,"inputTokens":332083,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.781,"originalCredits":0.781,"afterCompact":false},{"index":234,"time":"16:33","ratio":0.332828,"inputTokens":332828,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.817,"originalCredits":0.817,"afterCompact":false},{"index":235,"time":"16:34","ratio":0.333871,"inputTokens":333871,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.78,"originalCredits":0.78,"afterCompact":false},{"index":236,"time":"16:34","ratio":0.334109,"inputTokens":334109,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.759,"originalCredits":0.759,"afterCompact":false},{"index":237,"time":"16:35","ratio":0.334532,"inputTokens":334532,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.828,"originalCredits":0.828,"afterCompact":false},{"index":238,"time":"16:35","ratio":0.335814,"inputTokens":335814,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.827,"originalCredits":0.827,"afterCompact":false},{"index":239,"time":"16:35","ratio":0.336655,"inputTokens":336655,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.793,"originalCredits":0.793,"afterCompact":false},{"index":240,"time":"16:35","ratio":0.337089,"inputTokens":337089,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.786,"originalCredits":0.786,"afterCompact":false},{"index":241,"time":"16:36","ratio":0.337555,"inputTokens":337555,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.77,"originalCredits":0.77,"afterCompact":false},{"index":242,"time":"16:36","ratio":0.337785,"inputTokens":337785,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.774,"originalCredits":0.774,"afterCompact":false},{"index":243,"time":"16:36","ratio":0.33815,"inputTokens":338150,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.832,"originalCredits":0.832,"afterCompact":false},{"index":244,"time":"16:36","ratio":0.339359,"inputTokens":339359,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.798,"originalCredits":0.798,"afterCompact":false},{"index":245,"time":"16:37","ratio":0.339864,"inputTokens":339864,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.805,"originalCredits":0.805,"afterCompact":false},{"index":246,"time":"16:37","ratio":0.340522,"inputTokens":340522,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.79,"originalCredits":0.79,"afterCompact":false},{"index":247,"time":"16:37","ratio":0.341341,"inputTokens":341341,"outputTokens":null,"usageSource":"transcript-ratio","credits":0.824,"originalCredits":0.824,"afterCompact":false}],"identity":{"sumAttributed":54257794,"billedInputTokens":54257794,"absDiff":0,"ok":true},"pluginVersion":"2.7.4","selfCmd":"\"C:\\Users\\cat\\.qoder-cn\\plugins\\cache\\qoder-marketplace\\qoder-credits-inspector\\2.7.4\\bin\\credits-inspector.cmd\" cli","linkHealth":{"matched":0,"requests":247,"coverage":0,"usageSource":"transcript-ratio","breakdown":{"proxy":0,"transcriptTokens":0,"transcriptRatio":247},"logRecords":0,"lastRecordAt":null,"recentRequests":0,"proxyErrors":null,"clientAbort":null,"clientAbortMaxMs":null,"upstreamRejected":null,"aborted":null,"truncated":null,"noUsage":null,"slowestMs":null,"supersededFailure":null},"subagents":{"scanned":false,"dir":null,"probedPaths":["C:\\Users\\cat\\.qoder-cn\\projects\\E-----------electricity-vue-system\\2141a322-d02d-4c7f-ad01-ab0c8a4d7e14\\subagents","C:\\Users\\cat\\.qoder-cn\\projects\\2141a322-d02d-4c7f-ad01-ab0c8a4d7e14\\subagents"],"reason":"no-dir","count":0,"items":[],"totals":{"roundTrips":0,"billedInputTokens":0,"credits":0,"originalCredits":0},"combined":{"roundTrips":247,"billedInputTokens":54257794,"credits":143.885,"originalCredits":143.885}},"manual":null,"artifacts":{"report":"report.json","canvas":"请阅读这个项目，有详细的相关文档。阅读之后开始开__e73b3521.canvas.tsx"}} as unknown as Report;

function human(n: number): string {
  if (!isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(Math.round(n));
}

function pct(x: number): string {
  return (x * 100).toFixed(1) + "%";
}

// —— 可用性口径：把 report.availability 翻成人话，并决定某个数字该显示值、「≈」还是「—」——
const AVAIL_LABEL: Record<string, string> = {
  measured: "实测",
  derived: "推导",
  fallback: "回退",
  manual: "手工",
  unavailable: "不可用",
};

const SOURCE_LABEL: Record<string, string> = {
  "desktop-rich": "桌面端富转录",
  "ide-lite": "IDE 端精简转录",
  unknown: "来源未知",
};

function availOf(av: Availability | undefined, key: keyof Availability, dflt: Avail = "measured"): Avail {
  return (av?.[key] as Avail | undefined) ?? dflt;
}

/** 段标题旁的性质标注：实测不标（默认就是实测），其余标出来。 */
function availTag(av: Availability | undefined, key: keyof Availability, dflt: Avail = "measured"): string {
  const v = availOf(av, key, dflt);
  return v === "measured" ? "" : `（${AVAIL_LABEL[v]}）`;
}

/** 不可用的量显示「—」而不是 0：IDE 端的 0 是「读不到」，不是「没发生」。 */
function orDash(
  v: number,
  av: Availability | undefined,
  key: keyof Availability,
  fmt: (n: number) => string = String
): string {
  return availOf(av, key) === "unavailable" ? "—" : fmt(v);
}

function kindLabel(kind: string): string {
  switch (kind) {
    case "read":
      return "读取";
    case "write":
      return "写入";
    case "search":
      return "搜索";
    case "shell":
      return "命令";
    default:
      return "其他";
  }
}

function shortTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const p = (v: number) => String(v).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function SessionTokensReport() {
  if (!REPORT) {
    return (
      <ReportShell width="wide" ariaLabel="会话 token 消耗截析">
        <Stack gap="component">
          <H1>会话 token 消耗截析</H1>
          <Text tone="secondary">报告数据尚未注入。请在会话中触发一次 Stop 钩子，或运行 CLI 生成 report.json。</Text>
        </Stack>
      </ReportShell>
    );
  }

  const r = REPORT;
  const s = r.session;
  const c = r.context;
  const t = r.totals;
  const av = r.availability;
  const manual = r.manual ?? null;
  const sourceLabel = SOURCE_LABEL[r.source ?? "unknown"] ?? "来源未知";
  // 有没有 usage 决定整份报告是「真值/推导」还是「一律 —」。手工回填的 credits 不算有 usage，
  // 否则下面按 ratio 推导的图表会照画一堆 0。
  const hasUsage = availOf(av, "roundTrips") === "measured";
  const manualCredits = manual?.credits ?? null;

  // 报告里出现的命令一律用数据层注入的插件入口（Windows 上是自带启动器的绝对路径，免装 Node）。
  // 旧报告没有这个字段时回退原写法，不比改动前更差。
  const CLI = r.selfCmd || "node scripts/cli.mjs";

  // credits 覆盖范围：credits 与「计费输入总量」并排放在头部，读者一除就得到单价。
  // 混合会话里 credits 只来自转录路径的那几笔（实测某会话 413 笔里只有 1 笔带 credits），
  // 部分覆盖不说出来 = 把局部真值当全量展示。
  const cov = r.creditsCoverage ?? null;
  const covPartial = !!(cov && !cov.full && cov.roundTrips > 0);
  const covNote = covPartial ? ` · 仅覆盖 ${cov!.trips}/${cov!.roundTrips} 笔往返（占计费输入 ${pct(cov!.tokenShare)}）` : "";

  // 缓存命中：代理记录里一直带着 cachedTokens，此前采集了却从不呈现。
  const cachedMeasured = availOf(av, "cachedTokens") === "measured";
  const cachedText = cachedMeasured
    ? `输入 ${human(t.billedInputTokens)}，其中缓存命中 ${human(t.cachedTokens ?? 0)}（${pct(t.cachedShare ?? 0)}，${t.cachedTrips ?? 0} 笔往返带缓存）——供应商对重复叠加的前缀打折，故按 token 数的节省大于按计费的节省。`
    : "";

  // 代理告警只在「失败记录仍然成立」时才报：status.json 只写不清，陈旧失败会把用户推去改
  // 本来正确的 Base URL，越修越坏；而同一份报告里的 lastRecordAt / matched 是它在跑的硬证据。
  const proxyAlarm = r.proxy?.status && !r.proxy.statusSuperseded ? r.proxy.status : null;
  const proxyAlive = !proxyAlarm && (r.proxy?.logRecords ?? 0) > 0;

  // 官方 UI 一场会话扣费 = 主链 + 子代理，故有子代理时 headline 必须给 combined，否则比 UI 少一截
  const creditScope =
    r.subagents && r.subagents.count > 0
      ? {
          credits: r.subagents.combined.credits,
          note: `主链 ${t.credits} + 子代理 ${r.subagents.totals.credits} · 原始 ${r.subagents.combined.originalCredits}`,
        }
      : { credits: t.credits, note: `原始 ${t.originalCredits}（实测）` };

  const headline: MetricItem[] = [
    {
      label: "计费输入总量",
      value: hasUsage ? human(t.billedInputTokens) : "—",
      description: hasUsage
        ? availOf(av, "tokens") === "measured"
          ? `Σ prompt_tokens（实测真值）${covPartial ? ` · 覆盖全部 ${cov!.roundTrips} 笔往返` : ""}`
          : `Σ 上下文 × 往返${availTag(av, "tokens", "derived")}${covPartial ? ` · 覆盖全部 ${cov!.roundTrips} 笔往返` : ""}`
        : "转录无 usage",
    },
    {
      label: "当前上下文",
      value: hasUsage ? human(t.netContextTokens) : "—",
      description: hasUsage
        ? `峰值 ${pct(c.peakContextRatio)}${s.compactions ? "（压缩后起算）" : ""}`
        : "转录无 usage",
    },
    {
      label: "重发放大",
      value: hasUsage ? `${t.amplification}×` : "—",
      description: `往返 ${hasUsage ? `${s.roundTrips} 次` : "—"}`,
    },
    {
      label: "Credits",
      // 代理路径（自定义模型）token 是实测真值但 credits 无来源：hasUsage 为真也要显示「—」，
      // 否则 t.credits=0 会被读成「这次没花钱」。
      value:
        manualCredits != null
          ? `${manualCredits}`
          : hasUsage && availOf(av, "credits") !== "unavailable"
            ? `${creditScope.credits}`
            : "—",
      description:
        manualCredits != null
          ? `官方 UI 手工录入 · 本地 ${creditScope.credits}`
          : hasUsage && availOf(av, "credits") !== "unavailable"
            ? `${creditScope.note}${covNote}`
            : r.usageSource === "proxy" || r.usageSource === "transcript-tokens" || r.usageSource === "mixed"
              ? "自定义模型（BYOK）不经 Qoder 计费网关，无 credits 真值"
              : hasUsage
                ? "转录带 usage 但没有 credits 字段，本地无计费真值"
                : "本地不可用，见下方告警",
    },
  ];

  const pieData = r.byCategory.map((x) => ({ label: x.label, value: x.tokens }));

  // 系统提示词是报告里少数「用户能直接动」的一项：它每轮被完整重发，总量 = 每请求规模 × 往返数，
  // 而它的大小由装了多少插件与技能决定。占比小时不必啰嗦。
  const sysCat = r.byCategory.find((x) => x.key === "system");
  const sysNote =
    hasUsage && sysCat && sysCat.share >= 0.1
      ? ` 系统提示词占 ${pct(sysCat.share)}（${human(c.systemPromptTokens)}/请求 × ${s.roundTrips} 次往返 = ${human(sysCat.tokens)}）：它每轮都被完整重发，大小由你装了多少插件与技能决定，精简技能能直接压低这一项——本插件自己的技能描述也常驻在里面（约 170 token）。`
      : "";

  const tools = r.byTool.slice(0, 8);
  const toolCategories = tools.map((x) => x.tool);
  const toolSeries = [{ name: "重发 tokens", data: tools.map((x) => x.tokens) }];
  // 「单次」= 平均每次调用带来的重发 token。总量榜会埋掉次数少但每次极贵的工具：
  // 实测 Read 只 29 次却吃 335 万，单次是 Edit 的 2.6 倍。
  const toolRows = tools.map((x) => [
    x.tool,
    human(x.tokens),
    pct(x.share),
    String(x.calls),
    x.perCall == null ? "—" : human(x.perCall),
  ]);

  const files = r.byFile.slice(0, 20);
  const fileRows = files.map((f) => [
    f.label,
    kindLabel(f.kind),
    human(f.billed),
    pct(f.share),
    f.perRead == null ? "—" : human(f.perRead),
    String(f.trips),
    String(f.reads),
  ]);

  const byReq = r.byRequest ?? [];
  const reqLabels = byReq.map((x, i) => x.time || `#${x.index ?? i + 1}`);
  const reqInputs = byReq.map((x) => x.inputTokens);
  const reqCredits = byReq.map((x) => x.credits);
  // credits 只在覆盖全部往返时才画曲线：部分覆盖（BYOK 混合会话实测 413 笔里 1 笔有 credits）
  // 画出来是一地零，既读不出形状，还会暗示「其余往返没花钱」。
  const showCreditsChart = hasUsage && !covPartial && availOf(av, "credits") !== "unavailable";

  // 按类别下钻：类别小计行（accent/neutral）+ 其工具/文件明细行（default），用 rowTone 分组着色。
  const catDetail = r.byCategoryDetail ?? [];
  const detailRows: string[][] = [];
  const detailTones: ("accent" | "neutral" | "default")[] = [];
  for (const cc of catDetail) {
    const hasKids = cc.children.length > 0;
    detailRows.push([cc.label, "—", hasKids ? "小计" : "（整体，无工具/文件归属）", human(cc.tokens), pct(cc.share), "100%", "—"]);
    detailTones.push(hasKids ? "accent" : "neutral");
    for (const k of cc.children) {
      detailRows.push(["", k.tool || "—", k.label, human(k.tokens), pct(k.share), pct(k.catShare), String(k.trips)]);
      detailTones.push("default");
    }
  }

  // 子代理账：Agent 派发的子代理消耗不在主链里，单独一段呈现（无子代理则整段不渲染）。
  const sub = r.subagents;
  const subItems = sub?.items ?? [];
  const subRows: string[][] = [];
  const subTones: ("accent" | "default")[] = [];
  for (const a of subItems) {
    subRows.push([
      a.description || a.agentId,
      a.agentType || "—",
      String(a.roundTrips),
      human(a.billedInputTokens),
      pct(a.peakContextRatio),
      String(a.credits),
      String(a.originalCredits),
      a.error || "—",
    ]);
    subTones.push("default");
  }
  if (sub && subItems.length > 0) {
    subRows.push([
      "合计（主链 + 子代理）",
      "—",
      String(sub.combined.roundTrips),
      human(sub.combined.billedInputTokens),
      "—",
      String(sub.combined.credits),
      String(sub.combined.originalCredits),
      `主链 ${t.credits} / 子代理 ${sub.totals.credits}`,
    ]);
    subTones.push("accent");
  }

  // 压缩单笔成本：输入/摘要一律取数据层归一后的生效值（有代理实测就是实测，否则是客户端自估），
  // 并用「口径」列把两者分开——混着显示会让自估值被当成实测。
  const ccost = r.compactionCost;
  const ccItems = ccost?.items ?? [];
  const compactRows: string[][] = ccItems.map((e) => [
    `#${e.index}`,
    shortTime(e.at),
    e.trigger || "—",
    e.effectiveInputTokens == null ? "—" : human(e.effectiveInputTokens),
    e.effectiveOutputTokens == null ? "—" : human(e.effectiveOutputTokens),
    e.nextInputTokens == null ? "—" : human(e.nextInputTokens),
    e.savedTokens == null ? "—" : human(e.savedTokens),
    e.proxy && e.proxy.ms != null ? `${Math.round(e.proxy.ms / 1000)}s` : "—",
    e.proxy ? "实测" : "自估",
  ]);

  // IDE 端仅剩的真值：工具调用次数与文件读取次数（不依赖 usage，两种转录都写 tool_use 块）。
  const residueRows: string[][] = [];
  if (!hasUsage) {
    for (const x of r.byTool.filter((v) => v.calls > 0).sort((a, b) => b.calls - a.calls).slice(0, 15)) {
      residueRows.push([x.tool, "工具", String(x.calls), "—", "—"]);
    }
    for (const f of r.byFile.filter((v) => v.reads > 0).sort((a, b) => b.reads - a.reads).slice(0, 15)) {
      residueRows.push([f.label, kindLabel(f.kind), "—", String(f.reads), String(f.trips)]);
    }
  }

  // A1 三值：一次带 usage 的往返都没有时 0 <= max(2,0) 恒成立，会把「没数据」判成「校验通过」，
  // 故 breakdown.mjs 在 reqCount===0 时给 null；这里必须显示「不适用」而不是绿色通过。
  const identityText =
    r.identity.ok == null
      ? "恒等式 A1 不适用（本转录没有一次带 usage 的往返）。"
      : r.identity.ok
        ? `归因覆盖 ${pct(t.coverage)}${availTag(av, "categoryShare", "derived")}，恒等式 A1 通过（|Δ|=${r.identity.absDiff}）。`
        : `归因覆盖 ${pct(t.coverage)}，恒等式 A1 未通过（|Δ|=${r.identity.absDiff}）。`;
  // 窗口读不到时是静默回退的 200000，而所有 token 数字都乘它 —— 必须显式标 ≈ 与来源。
  const cwIsFallback = availOf(av, "contextWindow") === "fallback";
  const cwText = cwIsFallback ? `≈${human(c.contextWindow)}（回退值）` : human(c.contextWindow);
  // 当前占用头条：以最新上下文（currentContextTokens）为准；段内单调递增 ⇒ 旧版峰值与当前恒等，故合并为一条。
  const curCtx = c.currentContextTokens ?? c.peakContextTokens;
  const netAdv = c.netUserAdvice || c.peakUserAdvice || c.peakAdvice || null;
  const limitSrcLabel =
    c.userContextLimitSource === "manual" ? "手工"
    : c.userContextLimitSource === "config" ? "配置"
    : c.userContextLimitSource === "derived" ? "实测反推"
    : "默认";
  // 阈值来源非 manual/config/derived ⇒ 用的是内置兜底 200K，不是用户在 Qoder 设的真值，需显式提示如何改。
  const limitIsDefault = c.userContextLimitSource !== "manual" && c.userContextLimitSource !== "config" && c.userContextLimitSource !== "derived";
  // 上下文对比表：把旧版挤成一段小字的「界面显示 / 自动压缩实况 / 历史高点」拆成可扫读的行，无数据不占位。
  const ctxRows: string[][] = [];
  if (c.userContextLimit != null && Number.isFinite(c.contextWindow)) {
    ctxRows.push(
      c.contextWindow > c.userContextLimit
        ? ["Qoder 界面进度条", pct(c.netWindowRatio ?? c.peakContextRatio), `按模型物理窗口 ${cwText} 算，比你真实占比低约 ${(c.contextWindow / c.userContextLimit).toFixed(1)} 倍——界面显得偏空、有迷惑性，别信它`]
        : ["Qoder 界面进度条", pct(c.netWindowRatio ?? c.peakContextRatio), `按模型窗口 ${cwText} 算，与你实设上限一致，显示无偏差`],
    );
  }
  if (c.observedAutoCompactions != null && c.observedAutoCompactions > 0 && c.observedAutoTriggerTokens != null) {
    ctxRows.push(["自动压缩实况", `自动 ${c.observedAutoCompactions} 次`, `触发点在 ~${human(c.observedAutoTriggerTokens)}（窗口的 ${pct(c.autoTriggerRatio ?? 0)}）`]);
  } else if (s.compactions > 0) {
    ctxRows.push(["压缩实况", `手动 ${s.compactions} 次`, "本场未见自动压缩，均为你手动触发"]);
  }
  if (c.peakSessionNotable) {
    ctxRows.push(["压缩前历史高点", `${pct(c.peakSessionRatio ?? 0)}（${human(c.peakSessionTokens ?? 0)}）`, "本场曾达到的最高占用"]);
  }

  // 链路健康度：把 --request-log 的逐笔归因压成一行结论（明细属于排障，留在 CLI）。
  // 分两档：本会话确实走在代理链路上（proxy/mixed）才印「最近 N 笔」的失败统计与排障命令；
  // token 全来自转录的会话里，那些统计说的是别的会话，印成 warning + 命令是噪音，只留覆盖率与最近记录时间。
  // 纯官方模型用户从没配过代理，整行不渲染。
  const lhRaw = r.linkHealth ?? null;
  const lh = lhRaw && (r.proxy?.configured || lhRaw.matched > 0) ? lhRaw : null;
  const lhOnPath = !!lh && (lh.matched > 0 || lh.usageSource === "proxy" || lh.usageSource === "mixed");
  const lhErrors = lh ? lh.proxyErrors ?? 0 : 0;
  const lhBad = lhOnPath && lhErrors > 0;
  const lhTone: "info" | "warning" = lhBad ? "warning" : "info";
  // 三档拆分一行说清：代理实测 / 转录 input_tokens / 转录 ratio×window 各多少笔。
  // 旧报告（v3）无 breakdown 字段时退回到只报 matched。
  const lhBd = lh?.breakdown ?? null;
  const lhBdText = lhBd
    ? `拆分：代理 ${lhBd.proxy} 笔 · 转录 input_tokens ${lhBd.transcriptTokens} 笔 · 转录 ratio×窗口 ${lhBd.transcriptRatio} 笔`
    : "";
  const lhSrcNote = !lh
    ? ""
    : lh.usageSource === "proxy"
      ? "：全部为供应商实测"
      : lh.usageSource === "transcript-tokens"
        ? "：本会话 token 全来自转录 input_tokens（BYOK，代理未在链路上）"
        : lh.usageSource === "transcript-ratio"
          ? "：本会话 token 由 ratio×窗口推导（官方模型）"
          : lh.usageSource === "mixed"
            ? "：多源混合，以下拆分列为准"
            : lh.usageSource === "transcript"
              ? "：本会话 token 全部来自转录，代理不在链路上"
              : "";
  const lhText = !lh
    ? ""
    : [
        lh.requests > 0
          ? `代理覆盖 ${lh.matched}/${lh.requests} 笔往返（${pct(lh.coverage)}）${lhSrcNote}`
          : "本会话没有带 usage 的往返，代理无从覆盖",
        lhBdText,
        lh.lastRecordAt ? `代理最近记录 ${shortTime(lh.lastRecordAt)}` : proxyAlive ? "代理有记录但无时间戳" : "代理从未记到流量",
        lhOnPath && lh.recentRequests
          ? `最近 ${lh.recentRequests} 笔：代理失败 ${lh.proxyErrors} · 上游报错 ${lh.upstreamRejected} · 客户端提前断开 ${lh.aborted} · 成功但无 usage ${lh.noUsage}${
              lh.slowestMs != null ? ` · 最慢 ${Math.round(lh.slowestMs / 1000)}s` : ""
            }`
          : lhOnPath
            ? "请求诊断日志为空（运行中的代理是旧版，或还没记到）"
            : "",
        lhBad ? `有「代理失败」= 请求没出得去或代理自己抛了，跑 ${CLI} --request-log 看归因` : "",
        !lhOnPath && lhErrors > 0 ? `代理另有 ${lhErrors} 笔失败，属于走代理的那些会话` : "",
        lh.supersededFailure
          ? `${shortTime(lh.supersededFailure.at ?? null)} 那条「拉起失败（${lh.supersededFailure.error}）」已被之后的流量推翻，无需处理`
          : "",
      ]
        .filter(Boolean)
        .join("。") + "。";

  return (
    <ReportShell width="wide" ariaLabel="会话 token 消耗截析">
      <Stack gap="sectionCompact">
        <header>
          <Stack gap="component">
            <H1>会话 token 消耗截析</H1>
            <Text tone="secondary">
              {s.model || "未知模型"}
              {s.modelSource === "manual" ? "（手工录入）" : ""} · {sourceLabel} · 会话{" "}
              {String(s.id || "").slice(0, 8)}
              {s.title ? `「${s.title}」` : ""} · {shortTime(s.startedAt)} →{" "}
              {shortTime(s.endedAt)} · 压缩{" "}
              {availOf(av, "compactions") === "unavailable" ? "—" : `${s.compactions} 次`}
              {r.usageSource ? ` · usage 来源 ${
                r.usageSource === "proxy"
                  ? "代理实测"
                  : r.usageSource === "transcript-tokens"
                    ? "转录 input_tokens"
                    : r.usageSource === "transcript-ratio"
                      ? "转录 ratio×窗口"
                      : r.usageSource === "mixed"
                        ? "多源混合"
                        : "转录"
              }` : ""}
              {r.pluginVersion ? ` · v${r.pluginVersion}` : ""} · schema v{r.schemaVersion}
            </Text>
            <MetricsGrid variant="header" columns={4} items={headline} />
          </Stack>
        </header>

        {!hasUsage && r.source === "desktop-rich" && (
          <Callout tone="danger" title="本报告数值不可用：自定义模型（BYOK）不经 Qoder 计费网关">
            转录是桌面端富布局，但 assistant entry 里没有 message.usage——自定义模型（如千问
            tokenplan）的响应不经 Qoder 计费网关，credits / context_usage_ratio 无来源，提供商返回的
            usage 客户端也不落盘（已实测扫过 ~/.qoder-cn、~/.qoder、~/.qoder-cli 与 %APPDATA%\QoderCN）。
            本地补救（改一个配置文件即可，免装 Node、之后全自动）：编辑 {"`~/.qoder-credits-proxy/config.json`"}，
            把 {"`upstream`"} 填成你的供应商根地址（= Base URL 去掉结尾 /v1），再把自定义模型 Base URL 的 host:port
            换成 {`127.0.0.1:${r.proxy?.port ?? 49787}`}（路径保留），新开一个会话即自动拉起代理、按 message.id
            精确 join 出实测 token（credits 仍无真值）。装了 Node 也可用 {"`--setup-proxy`"} / {"`--check-proxy`"} 一步到位。
            手工回填通道同样可用：
            {"`.qoder-credits/overrides/<sessionId>.json`"}。
          </Callout>
        )}

        {!hasUsage && r.source !== "desktop-rich" && (
          <Callout tone="danger" title="本报告数值不可用：转录不含 message.usage">
            这是 {sourceLabel}（IDE 端客户端）。它的转录只写 session_meta / user / assistant / progress 四种 entry，
            message.usage 整个字段不存在，也不落到本地任何其它文件（已实测扫过 ~/.qoder-cn、~/.qoder、
            ~/.qoder-cli 与 %APPDATA%\QoderCN）。因此 Credits、token 与各类占比一律显示「—」而不是 0 ——
            0 会被误读成「这次没花钱」。真值只有官方 UI 有：把它填进{" "}
            {"`.qoder-credits/overrides/<sessionId>.json`"} 后重跑，上方会出现「官方 UI 真值」一段并与本地并列对账。
          </Callout>
        )}

        {manual && (
          <Callout tone="success" title="官方 UI 真值（手工录入，未覆盖任何本地数字）">
            Credits {manual.credits ?? "—"} · 原价 {manual.originalCredits ?? "—"}
            {manual.model ? ` · 模型 ${manual.model}` : ""}
            {manual.durationMin != null ? ` · 时长 ${manual.durationMin} min` : ""}
            {manual.startedAt ? ` · ${shortTime(manual.startedAt)}` : ""}
            {manual.localCoverage != null
              ? ` · 本地${manual.localScope === "combined" ? "合计（主链+子代理）" : "主链"} ${manual.localCredits ?? t.credits}，覆盖 ${pct(manual.localCoverage)}`
              : " · 本地无 usage，无法对账"}
            {manual.note ? `。备注：${manual.note}` : ""}
          </Callout>
        )}

        {sub && sub.count === 0 && sub.reason && sub.reason !== "no-dir" && (
          <Callout tone="warning" title={`子代理账未汇总（${sub.reason}）`}>
            探测过的候选目录：{(sub.probedPaths ?? []).join("  |  ") || "（无）"}
          </Callout>
        )}

        {proxyAlarm && (
          <Callout tone="warning" title={`代理自动启动失败（${proxyAlarm.error}）`}>
            {proxyAlarm.error === "EADDRINUSE"
              ? `端口 ${proxyAlarm.port ?? "—"} 被占用，自定义模型将无法对话。请换一个空闲端口重启代理：${CLI} --setup-proxy --port <新端口>，并把模型 Base URL 改成新端口。`
              : `代理未就绪（${proxyAlarm.error}），自定义模型可能无法对话。请运行 ${CLI} --check-proxy 查看链路状态。`}
          </Callout>
        )}

        {r.proxy?.dormant && !proxyAlarm && (
          <Callout tone="info" title="代理长期空闲">
            代理已配置但超过 14 天没有记录到流量（可能你已改回官方模型）。如不再使用自定义模型，可运行{" "}
            {CLI} --stop-proxy 停用代理；保留也不影响官方模型。
          </Callout>
        )}

        {cwIsFallback && (
          <Callout tone="warning" title="上下文窗口为回退值 200K（未从 runtime-config / usage 反推 / ManualTruth 拿到真值）">
            本会话所有以窗口为分母的占比与「峰值建议」都可能偏大，而绝对 token 数（已改以 S 真值为底）不受影响。
            建议在 {"`.qoder-credits/overrides/<sessionId>.json`"} 里手工填 {"`contextWindow`"}（如 qwen3-max=1000000），或等一笔带 input_tokens+ratio 的往返写入转录后自动反推生效。
          </Callout>
        )}

        {hasUsage && netAdv && c.userContextLimit != null && (
          <Stack gap="component">
            <Callout
              tone={netAdv.tone}
              title={`你真实的上下文占用 ${pct(c.netUserRatio ?? c.peakUserRatio ?? 0)}（${human(curCtx)} / 阈值 ${human(c.userContextLimit)}・${limitSrcLabel}）`}
            >
              {netAdv.text}
              {limitIsDefault && (
                <Text tone="secondary">
                  {` ⚙ 这里的 ${human(c.userContextLimit)} 是插件内置默认值，不是你在 Qoder「模型管理」里设的真实上限（插件读不到那个设置）。想按真实阈值算：在 ~/.qoder-credits-proxy/config.json 填 "userContextLimit": <你的上限>（对所有会话生效），或对本会话在 .qoder-credits/overrides/<会话id>.json 填同名字段，重跑报告即生效。`}
                </Text>
              )}
            </Callout>
            {ctxRows.length > 0 && (
              <Table
                headers={["对比口径", "数值", "说明"]}
                rows={ctxRows}
                density="compact"
              />
            )}
          </Stack>
        )}

        {c.thresholdNotEnforced && (
          <Callout tone="warning" title="⚠ 自动压缩不会在你设的阈值触发（本条最重要）">
            你的模型物理窗口是 {cwText}，Qoder 的自动压缩要等上下文涨到窗口 ~85%（≈{human(Math.round(c.contextWindow * 0.85))}）才触发；
            你在模型管理里设的 {human(c.userContextLimit)} 上限远在其下，永远不会触发自动压缩。
            请照上面「你真实的上下文占用」那条，到点自己手动压缩，别等它自动压。
          </Callout>
        )}

        {lh && (
          <Callout tone={lhTone} title="链路健康度">
            {lhText}
          </Callout>
        )}

        <Callout tone="info" title="度量口径">
          计费输入总量逐笔锁定真值（优先级：代理 promptTokens &gt; 转录 usage.input_tokens &gt; ratio{availTag(av, "contextRatio")} × {cwText}
          {cwIsFallback ? "，未拿到真窗口、全部 token 数字随之带 ≈" : ""}）；各类别/文件按块估算规模比例分摊
          {availTag(av, "categoryShare", "derived")}。{identityText}
          {cachedText ? ` ${cachedText}` : ""}
          {covPartial
            ? ` Credits 只来自 ${cov!.trips}/${cov!.roundTrips} 笔往返（占计费输入 ${pct(cov!.tokenShare)}），其余往返走代理实测、不经 Qoder 计费，故不要拿 Credits 去除以计费输入总量算单价。`
            : ""}
        </Callout>

        {byReq.length > 0 && (
          <ReportSection
            title="逐请求明细（每一次往返）"
            description={
              showCreditsChart
                ? "每个 round-trip 的真实输入规模与 credits；曲线骤降处为上下文压缩重置（顶部四项为会话累计，此处为每一次）"
                : `每个 round-trip 的真实输入规模；曲线骤降处为上下文压缩重置。credits 曲线未画：本会话只有 ${cov?.trips ?? 0}/${cov?.roundTrips ?? byReq.length} 笔往返带 credits（其余走代理实测、不经 Qoder 计费），画出来是一地零。`
            }
            meta={`${byReq.length} 次往返 · 压缩 ${orDash(s.compactions, av, "compactions")} 次`}
            divided
          >
            {showCreditsChart ? (
              <ChartComparisonGrid>
                <ChartContainer title="每次输入 tokens" ariaLabel="每次输入 tokens">
                  <LineChart
                    categories={reqLabels}
                    series={[{ name: "输入 tokens", data: reqInputs, tone: "info" }]}
                    height={220}
                    valueFormatter={human}
                    ariaLabel="每次请求输入 tokens"
                  />
                </ChartContainer>
                <ChartContainer title="每次 credits" ariaLabel="每次 credits">
                  <LineChart
                    categories={reqLabels}
                    series={[{ name: "credits", data: reqCredits, tone: "warning" }]}
                    height={220}
                    ariaLabel="每次请求 credits"
                  />
                </ChartContainer>
              </ChartComparisonGrid>
            ) : (
              <ChartContainer title="每次输入 tokens" ariaLabel="每次输入 tokens">
                <LineChart
                  categories={reqLabels}
                  series={[{ name: "输入 tokens", data: reqInputs, tone: "info" }]}
                  height={220}
                  valueFormatter={human}
                  ariaLabel="每次请求输入 tokens"
                />
              </ChartContainer>
            )}
          </ReportSection>
        )}

        {hasUsage && ccItems.length > 0 && (
          <ReportSection
            title="压缩单笔成本（会话里最贵的那几笔调用）"
            description="每次压缩本身就是一笔普通模型调用：整份上下文当 prompt 进去、摘要当 completion 出来。此前报告只显示「压缩 N 次」，把单笔最贵的开销藏成了一个计数。「省下」= 压缩前规模 − 压缩后首轮实测输入（地板）。口径列：实测=能在代理日志里唯一对上的供应商真值；自估=客户端在压缩边界里写的前后规模。"
            meta={`${ccItems.length} 次 · 输入累计 ${human(ccost?.totals.preTokens ?? 0)} · 摘要累计 ${human(ccost?.totals.postTokens ?? 0)} · 累计省下 ${human(ccost?.totals.savedTokens ?? 0)} · 其中 ${ccost?.totals.measured ?? 0} 笔为供应商实测`}
            divided
          >
            <Table
              headers={["第几次", "时刻", "触发", "输入", "摘要输出", "压缩后首轮", "省下", "耗时", "口径"]}
              rows={compactRows}
              density="compact"
              stickyHeader
            />
          </ReportSection>
        )}

        {sub && subItems.length > 0 && (
          <ReportSection
            title="子代理账（Agent 派发）"
            description="子代理的每次往返只写进它自己的独立转录，不计入上方主链任何数字；官方 UI 的一场会话扣费 = 主链 + 各子代理。"
            meta={`${subItems.length} 个子代理 · 主链 ${t.credits} + 子代理 ${sub.totals.credits} = 合计 ${sub.combined.credits} Credits`}
            divided
          >
            <Table
              headers={["子代理", "类型", "往返", "计费输入", "峰值占比", "Credits", "原价", "备注"]}
              rows={subRows}
              rowTone={subTones}
              density="compact"
              stickyHeader
            />
          </ReportSection>
        )}

        {hasUsage && r.byCategory.length > 0 && (
          <ReportSection
            title="按类别占比"
            description={`会话计费输入 token 在各来源间的分布（完整划分，占比之和 = 100%）${sysNote}`}
            meta={human(t.billedInputTokens) + ` tokens${availTag(av, "categoryShare", "derived")}`}
            divided
          >
            <ChartContainer ariaLabel="按类别占比">
              <PieChart donut data={pieData} centerLabel="计费输入" valueFormatter={human} />
            </ChartContainer>
          </ReportSection>
        )}

        {hasUsage && catDetail.length > 0 && (
          <ReportSection
            title="按类别下钻（工具 / 文件路径）"
            description="每个类别的消耗再拆到工具与具体文件/路径：工具返回、工具调用可精确到路径，其余类别为整体（无文件归属）。数值均为重发计费 token。"
            meta={`占总额=占计费输入总量 · 占本类=占该类别 · 程数=存活往返累计${availTag(av, "fileShare", "derived")}`}
            divided
          >
            <Table
              headers={["类别", "工具", "文件 / 路径", "重发 tokens", "占总额", "占本类", "程数"]}
              rows={detailRows}
              rowTone={detailTones}
              density="compact"
              stickyHeader
            />
          </ReportSection>
        )}

        {hasUsage && tools.length > 0 && (
          <ReportSection
            title="按工具占比"
            description="各工具相关内容（调用参数 + 返回）重发累计的计费 token 占比（仅工具相关块，非完整划分，占比之和 < 100%）。「单次」= 平均每次调用带来的重发 token：总量榜会把「次数少但每次极贵」的工具埋掉，这一列专门把它捞出来。"
            meta={`token 占比${availTag(av, "toolShare", "derived")} · 调用次数为实测`}
            divided
          >
            <Stack gap="component">
              <ChartContainer ariaLabel="按工具占比">
                <BarChart horizontal categories={toolCategories} series={toolSeries} valueFormatter={human} ariaLabel="按工具占比" />
              </ChartContainer>
              <Table
                headers={["工具", "重发 tokens", "占比", "调用", "单次"]}
                rows={toolRows}
                density="compact"
                stickyHeader
              />
            </Stack>
          </ReportSection>
        )}

        {hasUsage && fileRows.length > 0 && (
          <ReportSection
            title="按文件占比"
            description="精确到路径/文件名：内容随上下文被重复发送累计的计费 token 占比（Top 20，仅可归因文件的块）。「单次读取」= 这个文件平均每次被读取最终烧掉多少（含此后每一程的重发）——读一次就烧掉十几万的文件，在按总量排序的榜上毫不起眼。"
            meta={`程数=存活往返累计 · 读取=返回次数（实测）${availTag(av, "fileShare", "derived")}`}
            divided
          >
            <Table
              headers={["文件", "种类", "重发 tokens", "占比", "单次读取", "程数", "读取"]}
              rows={fileRows}
              density="compact"
              stickyHeader
            />
          </ReportSection>
        )}

        {!hasUsage && residueRows.length > 0 && (
          <ReportSection
            title="工具调用与文件读取（计数为实测）"
            description="占比与 token 一律不可用，但 tool_use 块两种转录都写，所以「谁被调了几次、谁被读了几次」仍是真值 —— IDE 端不是一无所有。"
            meta={`${t.toolCalls ?? 0} 次工具调用${availTag(av, "toolCalls")} · ${t.fileReads ?? 0} 次文件读取${availTag(av, "fileReads")} · ${s.turns} 轮对话`}
            divided
          >
            <Table
              headers={["工具 / 文件", "种类", "调用", "读取", "程数"]}
              rows={residueRows}
              density="compact"
              stickyHeader
            />
          </ReportSection>
        )}
      </Stack>
    </ReportShell>
  );
}
