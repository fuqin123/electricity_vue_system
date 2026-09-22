# 能源产效管理系统 · 开发记录及 Bug 总结

> 文档版本：v0.1.0 ｜ 最后更新：2026-09-22 ｜ 维护：前端 Owner
>
> **使用约定**：本文件是**追加式**记录。每完成一次迭代，在「第二章 迭代记录」新增一行；
> 每修一个 Bug，在「第四章 Bug 台账」新增一行；每踩一个坑，在「第五章 踩坑总结」补一条。
> 禁止删除历史记录（已解决的保留，标注状态即可）。

---

## 一、当前进度总览

| 项 | 状态 |
| :--- | :--- |
| 迭代版本 | `v1.1.0` |
| 交付日期 | 2026-09-22 |
| 大屏重构 | ✅ 完成（原生 JS → Vue 3.5 + Vite，视觉保持一致） |
| 后台管理系统 | ✅ 完成（21 个页面骨架，路由/菜单/跳转打通） |
| 浏览器视频监控 | ✅ 完成（go2rtc，mse → webrtc → hls → mjpeg 四协议自动降级） |
| 仓库结构统一 | ✅ 完成（前后端分离：`frontend/` + `backend/` + `deploy/` + `docs/`） |
| Git 仓库 | ✅ 已初始化（`main` 分支，含 `.gitignore` / `.gitattributes` / `.editorconfig`） |
| 后端接口 | 🟡 **已预留契约**（`backend/docs/api-contract.md` + `openapi.yaml`），实现待开发 |
| 共享层 | ✅ 完成（类型 / 配置 / Mock 引擎 / API SDK / 设计令牌） |
| 数据源 | 🟡 全部为虚拟数据（`dataConfig.mode = 'mock'`） |
| 业务函数 | ⬜ 留空（约定「仅框架」，写操作统一给出待接入提示） |
| 类型校验 | ✅ 通过（`pnpm typecheck` 零报错） |
| 生产构建 | ✅ 通过（大屏 8s / 后台 13s） |

---

## 二、迭代记录

| 版本 | 日期 | 内容 | 负责人 |
| :--- | :--- | :--- | :--- |
| v0.0.1 | 2026-09-21 | 原生 JS 版大屏交付（`frontend/`），双屏 1920×1080、Mock 数据、RTSP 视频降级方案 | 前端 |
| v0.1.0 | 2026-09-22 | 按技术栈文档重构为 pnpm monorepo；大屏迁移到 Vue 3.5；新增后台管理系统（21 页）；补齐 shared 共享层与交付文档集 | 前端 |
| **v1.1.0** | **2026-09-22** | **① 接入 go2rtc 浏览器视频监控（四协议自动降级，现场已验证）；② 仓库结构统一为前后端分离（`frontend/` + `backend/` + `deploy/` + `docs/reference/`）；③ 预留后端接口契约（api-contract + OpenAPI 21 接口）；④ 初始化 Git 仓库；⑤ 修复并行构建卡死问题** | 前端 |


---

## 三、本期范围与待接入清单

### 3.1 本期明确的范围边界

按《项目框架及技术栈.md》约定：**「仅框架：路由 / 菜单 / 页面骨架 / 跳转可通；所有业务函数留空」**。

因此本期的交付标准是：

- 所有路由可访问、菜单可点击、页面不白屏；
- 列表页的**查询 / 筛选 / 分页**真实可用（这部分属于数据层，已通过 Mock 打通）；
- 所有**写操作**（新增 / 编辑 / 删除 / 导出 / 下发 / 确认）统一调用 `notImplemented()`，
  给出「本期为框架阶段，业务函数待接入」的明确反馈，**不留「点了没反应」的假死感**。

### 3.2 待接入功能清单（按优先级排序）

> 接入方式：把下表「接入点」中的 `notImplemented(...)` 替换为真实 API 调用。
> 所有接口地址集中在 `frontend/packages/shared/src/api/index.ts`，页面代码无需改动。

#### P0 — 上线阻塞项

| # | 功能 | 接入点 | 备注 |
| :--- | :--- | :--- | :--- |
| 1 | 统一身份认证（SSO）登录 | `frontend/apps/admin/src/views/login/LoginView.vue` → `submit()` | **合规强制项**，不允许独立账号体系 |
| 2 | 路由级权限校验 + 后端接口鉴权 | `frontend/apps/admin/src/router/index.ts` → `beforeEach` | 当前仅校验本地 token 是否存在 |
| 3 | 大屏真实数据接口 | `dataConfig.mode` 改为 `'api'` | 返回结构见 `types.ts` 的 `EnergySnapshot` |
| 4 | 能耗强度参数回填 | `frontend/packages/shared/src/config.ts` → `energyModel` | 必须用真实历史数据，当前为行业经验值 |
| 5 | 告警阈值标定 | 同上 → `thresholds` | 需与能源管理员共同确认 |

#### P1 — 核心业务

| # | 功能 | 接入点 |
| :--- | :--- | :--- |
| 6 | 产线主数据增删改 | `views/production/LineListView.vue` |
| 7 | 计量点位维护与连通性测试 | `views/device/MeterView.vue` |
| 8 | 采集网关配置下发 | `views/device/GatewayView.vue` |
| 9 | 告警确认与批量确认 | `views/alarm/RealtimeView.vue` → `ack()` |
| 10 | 告警规则增删改 | `views/alarm/RuleView.vue` |
| 11 | 用户 / 角色 / 菜单 / 参数 / 字典 CRUD | `views/system/*.vue` |
| 12 | 报表生成与导出（xlsx / pdf / csv） | `views/energy/ReportView.vue` |
| 13 | 各类列表导出 | 各页面 `#actions` 中的导出按钮 |

#### P2 — 增强项

| # | 功能 | 说明 |
| :--- | :--- | :--- |
| 14 | 视频 RTSP 转流网关对接 | 后端侧工作；前端配置 `videoConfig.channels[*].flv` 即可 |
| 15 | 全局搜索 | `layout/TopNav.vue` → `onSearch()` |
| 16 | 报表定时任务 + 失败告警 | 需记录开始/结束时间、执行结果、影响行数，支持断点续跑 |
| 17 | 大数据量虚拟滚动 | 数据量上万后再上 `vxe-table` 替换 `el-table` |
| 18 | 数据字典驱动下拉框 | 当前下拉项为硬编码，应改为读 `dictType` |

### 3.3 接口清单（v1.1.0 已预留契约）

**完整契约见 `backend/docs/api-contract.md`（字段级说明）与 `backend/docs/openapi.yaml`（OpenAPI 3.0，21 个接口 / 23 个 schema）。**
本表仅为索引。

| 方法 | 路径 | 用途 |
| :--- | :--- | :--- |
| GET | `/api/energy/overview` | 大屏总览聚合数据（顶层必须含 `workshop`） |
| GET | `/api/energy/line/{lineId}` | 单线聚合数据 |
| GET | `/api/energy/hourly` | 原始小时明细（导出用） |
| POST | `/api/auth/login` / `logout` | 认证（正式环境替换为 SSO 回调） |
| GET | `/api/admin/overview` | 后台首页看板 |
| GET | `/api/admin/users` `roles` `menus` `params` `dicts` | 系统管理 |
| GET | `/api/admin/lines` | 产线主数据 |
| GET | `/api/admin/meters` `gateways` `quality` | 设备与采集 |
| GET | `/api/admin/alarms` · POST `/api/admin/alarms/{id}/ack` | 告警 |
| GET | `/api/admin/logs/collect` `operation` `login` | 日志 |
| GET | `/api/admin/reports` | 报表模板 |

> 按《代码仓库管理规范》要求，**后端 API 必须有 OpenAPI / Swagger 文档**，
> 且所有接口必须鉴权（白名单接口需评审通过）。契约文件已先行交付，
> 后端实现时直接按契约填充即可，前端无需改动。

### 3.4 视频监控链路（已实现，不经后端）

视频走**浏览器直连 go2rtc 网关**，不占用任何 Django 接口：

| 用途 | 地址 | 谁在用 |
| :--- | :--- | :--- |
| MSE 播放页（首选） | `{gateway}/stream.html?src={name}&mode=mse` | 大屏 iframe、后台预览 |
| WebRTC 播放页 | `{gateway}/webrtc.html?src={name}` | 大屏 iframe（降级） |
| HLS | `{gateway}/api/stream.m3u8?src={name}` | 大屏 hls.js |
| MJPEG | `{gateway}/api/stream.mjpeg?src={name}` | 大屏 `<img>` 兜底 |
| 抓图 | `{gateway}/api/frame.jpeg?src={name}` | 后台缩略图 |
| 流列表（自检） | `{gateway}/api/streams` | 后台「检测网关」 |

> 现场当前只接入 1 路摄像头（`my_camera` → `10.113.1.118` 通道 102），
> 其余 5 条产线在 `videoConfig.channels` 中标记 `enabled: false`，
> 摄像头到位后按 `deploy/go2rtc/README.md` 的「新增一路摄像头」步骤启用即可，
> **前端代码无需改动，只改配置**。

---

## 四、Bug 台账

> 状态取值：`已修复` / `待修复` / `无法复现` / `设计如此`

### 4.1 v1.1.0 修复的 Bug

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| B-201 | `pnpm build` 卡住 **13 分钟以上不结束**，单个 node 进程内存涨到 850MB；但单独执行 `pnpm -F @eep/admin build` 只要 21 秒 | 原 `build` 脚本是 `pnpm -r --filter "./apps/*" build`，pnpm 递归任务默认并发执行 → **两个 `vue-tsc` 同时跑**，各占约 1.5GB，8GB 机器触发换页 | 改为显式串行：`pnpm build:screen && pnpm build:admin`；`typecheck` 加 `--workspace-concurrency=1`。并在 `package.json` 与文档中写明不可改回并行 | `frontend/package.json` | 已修复 |
| B-202 | `daily-check.sh` 执行到 5xx 占比统计时报语法错误 | bash 算术展开 `$(( ))` **不支持三目运算符** `?:`，误用了 JS 风格写法 | 先算分母再算百分比 | `deploy/linux/daily-check.sh` | 已修复 |
| B-203 | `web.config.admin` 出现两个 `<httpProtocol>` 节点，IIS 启动站点报配置错误 | 分别添加「安全响应头」与「缓存策略」时各写了一个同名节点，XML 中同名元素只允许出现一次 | 合并为单个 `httpProtocol`，并移除全局 `Cache-Control: no-cache`（会把 assets 的长期缓存也一起干掉） | `deploy/windows/web.config.admin` | 已修复 |
| B-204 | `openapi.yaml` 解析失败 | `TrendPoint.output` 的 `description` 少了收尾引号，写成 `description: 产量（瓶）' }` | 补回引号，并用 PyYAML 全量校验通过（21 接口 / 23 schema） | `backend/docs/openapi.yaml` | 已修复 |
| B-205 | `VideoView.vue` 模板里用 `window.open(...)` 无反应 | Vue 3 模板表达式只暴露白名单全局变量，**`window` 不在其中** | 改为在 `<script setup>` 中定义 `openSnapshot()` 方法 | `frontend/apps/admin/src/views/video/VideoView.vue` | 已修复 |
| B-206 | 目录重构后大屏能跑、后台构建慢十倍 | 前端工作区整体移入 `frontend/` 后，`pnpm build` 的并行问题（同 B-201）被放大 | 同 B-201 | 同上 | 已修复 |

### 4.2 v0.1.0 修复的 Bug

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| B-001 | `pnpm install` 报 `ERR_PNPM_IGNORED_BUILDS`，安装中断 | pnpm v10+ 默认禁止依赖执行 `postinstall` | 在 `pnpm-workspace.yaml` 增加 `allowBuilds` 放行 `esbuild`/`vue-demi`/`@parcel/watcher` | `pnpm-workspace.yaml` | 已修复 |
| B-002 | 依赖看似装完，但 `vue-tsc` / `rollup` 等命令找不到，报 `Cannot find module` | Windows 未开「开发者模式」时，pnpm 默认的**符号链接式** `node_modules` 权限不足，只创建出**空目录** | 改 `nodeLinker: hoisted`，用硬链接的扁平布局规避符号链接 | `pnpm-workspace.yaml` | 已修复 |
| B-003 | 在 `.npmrc` 里写的 `node-linker` / `store-dir` 完全不生效 | pnpm v10 起工作区级设置**只读 `pnpm-workspace.yaml`**，`.npmrc` 中同名项被静默忽略 | 把设置迁移到 `pnpm-workspace.yaml` | `pnpm-workspace.yaml` | 已修复 |
| B-004 | 编译报 `Cannot find name 'T'`（`TablePage.vue`） | 泛型 SFC 的 `export interface` 会**脱离泛型作用域**，接口内引用泛型参数 T 无解 | 把 `TableColumn` 抽到独立的 `table-types.ts` | `components/table-types.ts` | 已修复 |
| B-005 | 编译报 `Type '...' is not assignable to type 'TableColumn<AlarmRecord>[]'` | TS 不给 `interface` 生成隐式索引签名，`Record<string, unknown>` 作默认值会导致函数参数逆变检查失败 | 泛型默认值取 `any`，并在注释中说明严格化方式 | `components/table-types.ts` | 已修复 |
| B-006 | 编译报 `Property 'unitKwh' does not exist on type 'never'` | 在 `forEach` 回调里给外部变量赋值，TS **不追踪闭包内赋值**，`worst` 被窄化成 `null` → 真值分支变成 `never` | 改用 `for...of` 循环，并落一个局部常量 `const w = worst` | `views/LineView.vue` | 已修复 |
| B-007 | `buildLine()` 返回值缺 `yesterday` 字段，类型不通过 | 把「今日」与「昨日」拆成两段式赋值（先构造再回填），中间态不满足 `LineData` | 把昨日同期计算**移入 `buildLine`**，一次算完 | `frontend/packages/shared/src/mock/energy.ts` | 已修复 |
| B-008 | `SysMenu` 菜单 mock 数据全类型报错 | 元组类型声明漏了一个元素（声明 5 个、实际写了 6 个），导致位次整体错位 | 元组类型修正为 6 元素并补注释 | `frontend/packages/shared/src/mock/admin.ts` | 已修复 |
| B-009 | 编译报若干 `TS6133: 'xxx' is declared but its value is never read` | 重构过程中留下的未使用导入/变量（`rng`、`protocols`、`rise`、`LineMaster`、`DeltaText` 等） | 逐一清理 | 多处 | 已修复 |

### 4.3 从旧版（v0.0.1）继承并已固化的口径类 Bug

这几个是**业务口径**问题，不是语法错误，但如果不修会导致大屏数字不可信。
旧版已修复，本次重构**完整保留**了修复逻辑，并加了注释防止后人改回去。

| ID | 现象 | 根因 | 修复方式（已固化到 `mock/energy.ts`） |
| :--- | :--- | :--- | :--- |
| B-100 | 整点刚过时，全线单位电耗虚高到约 20 kWh/千瓶，触发满屏误报 | 当前小时**产量按已过分钟缩放、电量却按固定比例缩放**，两者比例不一致 → 电量/产量虚高 | 当前小时用**同一比例 `frac` 同时缩放产量与电量** |
| B-101 | 单位电耗在同一分钟内反复跳动，数值不稳定 | 刚过整点时电量很小，`Math.round` 取整带来 ±0.5 kWh 误差，除以小产量后放大成明显跳动 | 电量 **小于 20 kWh 时保留 1 位小数**，不做整数取整 |
| B-102 | 整点时刻产线被误判为「低产」 | 状态判定用了**已缩放的当前小时产量**，整点刚过时数值必然很小 | 状态判定改用 **`projected`（整点预期值）**，并把 `progress < 1/4` 的低产告警压住不报 |
| B-103 | 日达成率与月度柱状图口径打架 | 日达成率分母用了「全天计划」，而统计窗口只到当前小时 | 日达成率分母改为 **`planElapsed`（截至当前小时的应产）**，全天计划另存 `planFull` |
| B-104 | 年累计产量与「日均 × 已过天数」推算不一致 | 年累计直接累加，与日均推算口径不同 | 年累计统一按 **日均 × 年内已过天数** 推算 |
| B-105 | 同一时刻反复刷新大屏，数字会跳变 | Mock 用了 `Math.random()`，每次调用结果不同 | 改用 **FNV-1a 哈希 + mulberry32** 做种子确定性随机，种子为 `产线|日期|小时` |
| B-106 | 未采集时段被补 0，大屏显示成「产量暴跌」 | 数据源把 null 当 0 处理 | 未采集时段一律置 `null` + `future: true`，前端渲染成**虚线柱（计划态）**，绝不造假曲线 |
| B-107 | 告警面板被单条线刷屏 | 每条线每个异常时段各报一条 | 告警按「**同类合并**」：连续停线合并为一条，单位电耗只报当日最差的一个已完成整点 |
| B-108 | 视频未配置时 6 路通道各报一条告警 | 逐线生成告警 | 合并为一条「视频通道未配置转流网关（N 路：…）」 |

### 4.4 已知问题 / 遗留风险

| ID | 问题 | 影响 | 处理计划 |
| :--- | :--- | :--- | :--- |
| K-001 | `energyModel` 强度参数为行业经验值，非实测 | 折算指标（单位电耗、水/汽耗强度）不能直接用于考核 | 试运行 1 个月后用真实历史数据回归拟合 |
| K-002 | 大屏 `ECharts` 整包引入，产物 gzip 后约 343 KB | 内网首屏加载约 1—2 秒，可接受 | 若要优化，改为按需引入 `echarts/core` + 具体图表类型 |
| K-003 | 后台 `element-plus` 全量引入，产物 gzip 后约 342 KB | 同上 | 后续可按需引入 + `unplugin-vue-components` |
| K-004 | `useViewport`（`composables/useEcharts.ts`）当前无调用点 | 死代码 | 下次迭代确认是否需要，不需要则删除 |
| K-005 | 后台下拉框选项为硬编码，未读数据字典 | 字典与页面可能不一致 | 迭代 P2-#18 统一改为字典驱动 |
| K-006 | 大屏非 16:9 屏幕四周留黑边 | 观感问题，非缺陷 | 属预期行为，见 `01-项目介绍` Q6 |
| K-007 | 视频首次播放依赖动态 import `flv.js` / `hls.js` | 首次切换产线有约 300—600 ms 空白 | 可接受；若要消除，改为首屏预加载 |

---

## 五、踩坑总结（给后来人的提醒）

### 5.1 pnpm v10+ 的三处行为变更

**① 设置项的存放位置变了。**
`.npmrc` 仍管 `registry`，但 `node-linker`、`store-dir`、`onlyBuiltDependencies` 等
**工作区级设置必须以 `pnpm-workspace.yaml` 为准**，写在 `.npmrc` 里会被静默忽略——
不会报错，只会「没生效」，非常难排查。踩坑表现见 B-003。

**② 依赖的安装脚本默认被禁止。**
`esbuild`、`vue-demi` 这类依赖需要通过 `postinstall` 建立平台二进制或软链，
被 pnpm 拦下后表现为构建时报「平台二进制缺失」或 `vite` 启动失败。
必须显式放行：

```yaml
# pnpm-workspace.yaml
allowBuilds:
  esbuild: true
  vue-demi: true
  '@parcel/watcher': true
```

**③ Windows 符号链接权限。**
pnpm 默认用符号链接组织 `node_modules`，在未开启「开发者模式」的 Windows 上
会因权限不足**只创建空目录**，且不报错。现象是「依赖明明装完了，但命令找不到、模块 import 不到」。

两种解法，任选其一：

```powershell
# 解法 A（推荐，改配置，团队统一）
# pnpm-workspace.yaml
nodeLinker: hoisted
```

```powershell
# 解法 B（改系统设置，保留符号链接的效率）
# 设置 → 隐私和安全性 → 开发者选项 → 打开「开发人员模式」
```

本项目采用**解法 A**，这样不依赖开发机的系统设置，新同事 clone 下来直接能跑。

### 5.2 TypeScript 的三个经典陷阱

**① `interface` 没有隐式索引签名。**
`interface Foo { a: string }` **不能**赋值给 `Record<string, unknown>`——TS 只为**类型别名/对象字面量**
生成隐式索引签名，不给 `interface` 生成。所以在写泛型约束时：

```ts
// ❌ line 会报错：LineMaster 不满足 Record<string, unknown>
function f<T extends Record<string, unknown>>(row: T) {}

// ✅ 用 object，需要按字符串取属性时显式 cast
function f<T extends object>(row: T) {
  const v = (row as Record<string, unknown>)[key];
}
```

**② 泛型 SFC 里不能 `export` 引用泛型参数的接口。**

```vue
<!-- ❌ 报 "Cannot find name 'T'" -->
<script setup lang="ts" generic="T extends object">
export interface Col { formatter?: (row: T) => string }
</script>
```

因为 `export` 会被提升到泛型作用域之外。正确做法是把类型**抽到独立的 `.ts` 文件**，
但抽出去后它就拿不到 T 了——所以这类「列定义」类型的泛型默认值只能取 `any`，
或者由调用方在每个页面显式声明 `TableColumn<LineMaster>[]`。本项目采用后者可选、前者兜底。

**③ 闭包内的赋值不被控制流分析追踪。**

```ts
let worst: HourPoint | null = null;
hours.forEach((s) => { if (bad(s)) worst = s; });
if (worst) { worst.hour }   // ❌ TS 认为 worst 恒为 null → 属性类型变 never
```

TS 不会为了闭包去推断「循环结束后 worst 可能有值」。**改用 `for...of`** 即可正常窄化，
顺手再落一个局部常量 `const w = worst`，避免后续再出现同类问题。

### 5.3 能源数据的三条铁律（业务侧）

1. **电表值要先差分再用。** 电表上报的是累计电能，换表/归零/断讯会在差分后产生尖峰，
   采集侧必须过滤，否则那一个小时的电耗会是个天文数字。
2. **不要给未采集的时段补 0。** 补 0 会在大屏上显示成「产量暴跌」，
   让人误以为产线停了。要置 `null`，让前端按「计划态」渲染。
3. **单位电耗必须和产量用同一时间口径。** 这是本项目最隐蔽的一个坑（见 B-100）：
   一个按分钟缩放、一个按固定比例缩放，比例一错，整屏告警全误报。

### 5.4 前端工程侧

- **`<script setup>` 的 `defineExpose` 在模板里不能直接用**，父组件必须通过 `ref` 拿实例。
  本项目避开了这个复杂度：`HourTable` 的统计值改由父组件自己算，直接传 props 给 `QualityBars`。
- **Vite 的 `base` 配置决定部署路径。** 大屏用 `base: './'`（相对路径，任意层级可部署）；
  后台用 `base: '/'`（history 模式需要绝对路径配合服务端 rewrite）。改部署路径时**两处都要改**。
- **SCSS 变量通过 `additionalData` 全局注入**（见两个 `vite.config.ts` 的 `css.preprocessorOptions`），
  组件内可直接用 `$yl-primary` 这类变量，无需逐个 `@use`。

---

## 六、设计决策记录（ADR）

| # | 决策 | 理由 | 放弃的方案 |
| :--- | :--- | :--- | :--- |
| ADR-001 | 大屏与后台拆成两个独立应用 | 路由模式、视觉体系、优化目标三者都冲突；详见 `01-项目介绍` §2.2 | 单应用 + 多布局（样式互相污染，history/hash 无法共存） |
| ADR-002 | 大屏保留 1920×1080 固定舞台 | 保证不同分辨率下构图完全一致，数字位置不漂移 | 响应式栅格（不同屏得到不同构图） |
| ADR-003 | 共享层直接引用源码（不预编译） | 改一处即时生效，两个应用无需重新构建依赖包；通过 `vite.resolve.alias` + `tsconfig.paths` 指向 `src` | 预编译为 `dist` + 声明文件（增加构建环节，调试链路变长） |
| ADR-004 | 泛型默认值用 `any`（`TableColumn<T = any>`） | 规避 TS 索引签名限制，避免 19 个页面都要写泛型 | 强制每个页面声明泛型（改动面大，收益有限） |
| ADR-005 | 写操作统一走 `notImplemented()` | 本期约定「业务函数留空」，但不能让用户点了没反应 | 空实现的按钮（假死感）；或直接隐藏按钮（看不见完整骨架） |
| ADR-006 | 后台不使用第三方品牌 logo | 自用业务系统不应出现云厂商商标；改为按设计系统规范绘制自有标识 | 直接复用 `Volcengine_design` 的 nav-logo |
| ADR-007 | 阈值以「系统参数」为唯一真值来源 | 避免告警规则与页面展示各写一套阈值 | 前端硬编码阈值（改一次要发一次版） |

---

## 七、交付前自测清单

供每次迭代回归使用，勾选前请实际验证而非目测。

### 7.1 工程

- [ ] `pnpm install` 从零可完成，无报错
- [ ] `pnpm typecheck` 全部工作区零报错
- [ ] `pnpm build` 两个应用均构建成功
- [ ] 构建产物中无 `console.log` 遗留（`main.ts` 里的错误上报除外）

### 7.2 大屏

- [ ] `#/overview` 与 `#/line/A1`～`#/line/A6` 均可访问
- [ ] 快捷键 `1` / `2` / `F` / `R` 生效
- [ ] 点击总览的产线行能跳到对应单线详情
- [ ] 趋势维度切换（年/月/日/时）图表与说明文字同步变化
- [ ] 1920×1080、2560×1440、3840×2160 三种分辨率下无裁切、无错位
- [ ] 按 `F` 全屏后挂机 ≥ 8 小时，观察内存与数值刷新是否正常
- [ ] 跨零点时确认「今日」归零、「昨日」正确落到前一天
- [ ] 关掉某个图表容器尺寸后（缩小窗口）图表能自适应

### 7.3 后台

- [ ] 未登录访问任意页面自动跳登录页
- [ ] 登录后 21 个菜单项**逐个点击**均能正常打开，无 404、无白屏
- [ ] 刷新任一子路由不 404（history 回退配置正确）
- [ ] 侧边栏折叠/展开正常，刷新后保持折叠状态
- [ ] 每个列表页的查询、重置、分页均可用
- [ ] 所有写操作按钮点击后有明确反馈（不是静默无响应）
- [ ] 面包屑与实际路由一致

### 7.4 接口切换

- [ ] 把 `dataConfig.mode` 改为 `'api'` 并指向真实接口，页面无报错、数值与能源系统对得上
- [ ] 断开某条线的电表，页面应在「数据质量」显示缺失率，并出现「电表无数据」告警
- [ ] 视频网关配好后，A1 详情页左上角出图；**关掉网关应降级为占位而不是白屏**

---

## 八、上线前必须处理的事项

| # | 事项 | 说明 |
| :--- | :--- | :--- |
| 1 | 接入公司统一身份认证（SSO） | 合规强制项，不允许独立账号体系 |
| 2 | 回填真实能耗强度参数与告警阈值 | 否则所有折算指标不可用于考核 |
| 3 | 补齐本文档「维护人」表 | 《代码仓库管理规范》要求 |
| 4 | 后端接口按 OpenAPI 规范出文档 | 同上 |
| 5 | 生产环境关闭调试开关 | 大屏的屏幕异常提示条已按 `import.meta.env.PROD` 自动关闭，无需手工操作 |
| 6 | 确认 CORS 白名单、SQL 注入防护、审计日志 | 同上 |
| 7 | 确认第三方依赖许可证 | 严禁 GPL 等强传染性许可证 |
| 8 | 依赖巡检 | 每月一次，关注 CVE 安全公告 |
