# 能源产效管理系统 · 开发记录及 Bug 总结

> 文档版本：v1.4.3 ｜ 最后更新：2026-09-29 ｜ 维护：前端 Owner / 后端 Owner
>
> **使用约定**：本文件是**追加式**记录。每完成一次迭代，在「第二章 迭代记录」新增一行；
> 每修一个 Bug，在「第四章 Bug 台账」新增一行；每踩一个坑，在「第五章 踩坑总结」补一条。
> 禁止删除历史记录（已解决的保留，标注状态即可）。

---

## 一、当前进度总览

| 项 | 状态 |
| :--- | :--- |
| 迭代版本 | `v1.4.3` |
| 交付日期 | 2026-09-29 |
| 大屏重构 | ✅ 完成（原生 JS → Vue 3.5 + Vite，视觉保持一致） |
| 后台管理系统 | ✅ 完成（21 个页面骨架，路由/菜单/跳转打通） |
| 浏览器视频监控 | ✅ 完成（go2rtc；大屏默认 **mse → hls → mjpeg**，可按 V 键切换链路；后台预览走 mse）<br>✅ v1.1.1 修复大屏「有声无画」（B-209 容器高度为 0） |
| 仓库结构统一 | ✅ 完成（前后端分离：`frontend/` + `backend/` + `deploy/` + `docs/`） |
| Git 仓库 | ✅ 已初始化（`main` 分支，含 `.gitignore` / `.gitattributes` / `.editorconfig`） |
| 后端采集 | ✅ 完成（13 路电表 + 蒸汽总表；现场实测 14/14 点位 1.4 秒，MySQL 入库） |
| 小时用量（差值） | ✅ 完成（缺数/换表/尖峰/偏离整点四类异常均已处理，`python manage.py test apps.energy` 71 个单元测试覆盖） |
| 后端接口 | ✅ 能源只读接口已实现（`/api/energy/*`，v1.4.0 新增 `dashboard`）；✅ 后台配置接口打通（`/api/admin/*`，网关/计量点增删改与试采；v1.4.0 新增 `params` 读写与 `readings` 仪表读数查询）；🟡 产量接口已建表并如实返回 **null + available=false**（演示占位不是真实产量）；系统管理类接口中「系统参数」已接真实库，用户 / 角色 / 菜单 / 字典 / 告警仍待开发 |
| 数据库 | ✅ v1.3.0 重新设计（库名 `eep_energy`，表名按业务语义加前缀）：`production_line` / `energy_type` / `energy_gateway` / `energy_meter` / `energy_raw`（原始读数）/ `energy_hourly`（小时差值）/ `energy_daily`·`energy_monthly`·`energy_yearly`（周期总计，按「产线 × 能源类型 × 周期」）/ `production_hourly` / `energy_collect_log`，均已在库并驱动大屏。详见 `backend/docs/数据库设计-v1.3.0.md` |
| 共享层 | ✅ 完成（类型 / 配置 / Mock 引擎 / API SDK / 设计令牌） |
| 数据源 | ✅ v1.2.1 起大屏切真实接口（`dataConfig.mode = 'api'`）：电量/汽量为真实采集数据；产量统一 **null**（数据源未接入，界面显示 `--`） |
| 业务函数 | 🟡 设备管理（网关/计量点）读写已接真实接口；其余写操作仍走 `notImplemented()` 待接入提示 |
| 类型校验 | ✅ 通过（`pnpm typecheck` 零报错） |
| 生产构建 | ✅ 通过（v1.3.1 实测：大屏 9.3s / 后台 12.0s，串行） |

---

## 二、迭代记录

| 版本 | 日期 | 内容 | 负责人 |
| :--- | :--- | :--- | :--- |
| **v1.4.3** | **2026-09-29** | **大屏跨零点自动翻页 + 昨天那一根的空窗（现场报「每天 23 点数据出来后趋势图固定在前一天，要手动刷新浏览器」）：① B-527 —— `selectedDate` 只在 store 创建时取一次当天日期，`isHistory` 里那句 `isoToday()` 又**没有响应式依赖**（`new Date()` 不是依赖，computed 求值一次永久缓存），于是零点后接口继续按「已经过完的一天」返回昨天，画面整日停在昨天；改为 `today` 反应式 + 每轮定时器先 `syncToday()` 对表，未被用户显式选过日期时自动翻到新一天（`userPicked` 钉住历史时**不拽回**，夜里回看昨天不该被零点静默改写），`DateSwitch` 内部的 `today` 同因，改由上层传入；② B-528 —— v1.4.2 把汇总降为每天 00:30 结算，但「昨天」那行上一次写入还是前天 00:30，导致新一天 00:00—00:30 大屏「近 30 日」的昨天那一根空一晚；加 `summary._day_not_settled()`：**只有今天与昨天**在汇总行缺失或 `covered_count < expected_count` 时退回小时表现算（限两个周期，长序列成本不涨），同时让 `EEP-Rollup` 停摆时退化成现算而不是给残缺旧值。补 2 个测试（共 71 个）。跨零点用改时钟的实测验证：加载后把时钟推过零点、**不刷新页面**，日期控件自动变 09-30、徽标仍「实时」、并自发出一次 `overview?date=2026-09-30` 请求；生产侧同时确认 `EEP-Rollup` 已在 00:30:01 成功结算（上次结果 0，昨天覆盖数 24）** | 全栈 |
| **v1.4.2** | **2026-09-28** | **周期取数规则与汇总节奏（现场两项决定）：① 确认「partial / 异常小时不进日、月合计」维持 ADR-016 不变（09-22 那 23 条 679.74 kWh 仍被整段剔除，日/月图上如实留空）；② 日 / 月 / 年汇总由「每小时跟着采集刷」改为每天 00:30 **结算一次**（新计划任务 `EEP-Rollup` → `summarize --rollup`，窗口 = 昨天 + 今天 + 近 2 月 + 今年），`collect --aggregate` 与 `collect_loop` 不再刷汇总（要刷得显式加 `--summarize`）。配套一条取数规则并落在**一个函数**里：`summary.is_open_period()` —— **包含今天的周期一律从小时表现算，已结束的周期才读汇总表**，`period_rows()` 内实现，大屏与后台经同一个函数出数，所以当天不会出现「大屏在动、后台停着」或反之。这样才会同时满足两件事：汇总不必每小时重扫，而大屏的「今日 / 本月 / 本年」仍每个整点更新（未来的小时在 `energy_hourly` 里本来没有行，现算天然等于「截至此刻」）。新增入口 `scripts/daily-rollup-task.py`（复用 `collect_task.run_task`，无控制台接管 stdout 那个坑不重复踩），日志 `logs/rollup-task.log`；⚠ 这个任务停掉**不报错**，只会让「近 30 日 / 近 12 月」无声停在最后一次结算那天，已写进每日巡检第 10 项。补 5 个测试（共 69 个），含「把今天的日行改成 999，当期数字必须不受影响」与「昨天读汇总表不现算」两条对冲断言** | 全栈 |
| **v1.4.1** | **2026-09-28** | **大屏与后台数字对齐（纯缺陷修复，无新功能）：现场报「09-27 22 点一线 7.8 / 大屏 7.3」与「每天 23 点数据离谱」，逐格比对接口后修掉四处独立成因 —— ① 查历史日时第 23 小时被当成「进行中」，而实时窗口只写下界，把那天 23:04 之后直到此刻的全部读数减成一格（一线 85 对真值 8.1、三线 3285 对 343.1），日累计一起虚高（B-522：`hourly_cells` 增加显式 `closed`，快照新增 `ongoingHour`/`dayClosed`，`currentHour` 退回纯排序锚点）；② `collect --aggregate` 只刷今天的日汇总，而昨天 23 点要等今天 00:04 才算得出来，于是**每一天都永远少最后一个小时**、月/年按日累加跟着一起偏小（B-523：抽出 `summary.refresh_rollups()` = 昨天 + 今天 + 近 2 月 + 今年，两条采集路径共用；已回填 09-23 起历史日）；③ 大屏对 ≥20 kWh 取整而后台保留小数，几乎每行都差 0.x（B-524：电量口径统一 1 位小数，新增 `dec1()`）；④ 切日期时旧快照挂在新的日期标签下、连切两次还有后发先至的覆盖（B-525：请求序号丢弃过期响应 + `stale` 显式提示「正在取数」，7.3 实测正是 09-25 同格的值）；另修一处自己引入的回归：把 `rateNow` 改可空却没改 `types.ts`，总览整片渲染失败（B-526）。补 7 个测试（共 64 个），其中**「大屏每小时数字 == /api/energy/range 线别序列」逐格断言**是这次的新锁；接口层实测 09-23…09-27 六线 × 24 小时 + 日累计 **0 处不一致** | 全栈 |
| **v1.4.0** | **2026-09-24** | **后台数据视图重构 + 成本口径落地（四条需求 + 三条缺陷）：① 数据中心加「年-月-日 时 → 年-月-日 时」起止筛选，主趋势图改为**单箱变动费用**（叠加每月标准变动费用虚线），新增电 / 水 / 汽三张**按能源分的累计趋势图**（累计用量 · 累计产量 · 累计单耗），删除「今日用量统计」改为区间累计；② 用量查询标题改**区间能源趋势图**，电 / 水 / 汽曲线**多选**、车间合计带水汽、加**线别筛选**（选单线时水汽勾选禁用并注明「无独立表」），三个计量页同样加时间与线别筛选；③ 设备与采集新增**仪表读数查询**（按时间 / 网关 / 仪表查 `energy_raw` 原始累计读数 + 报文，分页与 CSV）；④ 新增 `system_param` 表与 `seed_params` 命令，后台「系统参数」页从演示数据切为**真实读写**，成本算料（电价 / 水价 / 汽价 / 箱规 / 标准费用）从此处统一维护；⑤ 修**电 / 水 / 汽三页明细表恒为空**（TablePage 只在挂载时取一次数，快照还没回来 —— 现场报的是「汽计量无法查询」，实测三页同病，B-519）、**水汽明细列宽固定导致右侧大片空白**（B-520）、**图表 yAxisIndex 算错导致 mounted 抛异常、连带同页表格一列都渲染不出**（B-521）；⑥ 新接口 `GET /api/energy/dashboard`、`GET/PUT /api/admin/params`、`GET /api/admin/readings`，`/api/energy/range` 的 `from`/`to` 升级为小时精度（纯日期写法保持兼容）；⑦ 后端补 13 个测试（共 57 个），缺数与未标定的口径全部锁死** | 全栈 |
| **v1.3.1** | **2026-09-24** | **大屏布局与小时状态口径（纯前端）：① 总览中列末块「六条产线对比」写死了 `height="var(--c3)"`，而 `PanelBox` 只有在不传 height 时才 `grow` —— 左/右两列的末块都是自适应撑满，于是中列底边比两侧短约 24 舞台 px，三列底边参差（B-517）。去掉固定高度与无用的 `--c3`，实测三列末块底边同为同一值、画布 958×206 → 958×244；② 「本线小时明细」把已结束的异常整点显示成**「进行中」**（A5 线 08:00 / 09:00 实测），根源是后端 `partial` 的含义是「合计不完整」（当前小时 + 有表被排除的历史整点共用一个布尔），而 `HourPoint` 类型**没声明后端一直在发的 `quality`**，前端只能猜（B-518）。改为「进行中」只认 `h === currentHour`，已结束整点按 `quality` 显示 `异常·缺数 / 异常·换表 / 异常·尖峰 / 异常·部分缺数`，文案集中在 `HOUR_QUALITY_LABEL` 单一来源，「今日分时」tooltip 同步，mock 引擎补齐 `quality`；③ 顺手补回 v1.3.0 插入时被弄丢的 v1.2.4 小节标题，并把 Bug 台账编号重排为 4.0(v1.3.1) → 4.1(v1.3.0) → 4.2(v1.2.4)…** | 前端 |
| **v1.3.0** | **2026-09-24** | **数据库重新设计（按新表结构重建，仅保留 09-23/09-24 两日数据）：① 表名前缀统一为业务语义 —— `production_line` / `energy_type` / `energy_gateway` / `energy_meter` / `energy_raw` / `energy_hourly` / `production_hourly` / `energy_collect_log`，原单表 `eep_period_summary` 拆成 `energy_daily` / `energy_monthly` / `energy_yearly`（按「产线 × 能源类型 × 周期」，唯一键在此三元组上）；② 新增 `energy_type` 维度表（水/电/汽），`meter.kind` 字符串改为外键，单位与名称只有一处定义；③ 新增**设备表 `energy_meter`**（原 `eep_meter` 演化）—— 采集主体是「表」而不是产线，所以 `energy_raw` 的唯一键落在 `(meter_id, collect_time)`，你要的「按产线+能源类型+时间查」由普通索引覆盖；④ 车间水/汽总表挂**车间哨兵行**（`line_type=workshop`），使 `line_id` 全表非空、唯一键处处有效；⑤ **新增 `energy_hourly` 小时用量表**：日/月/年一律由它累加，绝不用「日末读数 − 日初读数」（后者在采集中断时会静默吞掉缺的小时）；⑥ 迁移采用「改名保数据」+ 分批操作（0005 改名 / 0006 结构+回填 / 0007 收紧非空），并顺带修掉库里 1 条历史重复读数；⑦ 对外接口路径与响应结构**一字未改**，前端零改动** | 后端 |
| **v1.2.4** | **2026-09-23** | **前端观感修复（纯前端，不动接口）：① 总览下方「六条产线对比」面板此前几乎全空（958px 画布只有左边 40px 有线名）—— 柱值全为 null 时 ECharts 连右侧标签一起不画，现改为与单线趋势图同一套自适应口径：产量未接入 → 条长＝今日用电量、右侧＝「用电量 · 占车间合计%」，缺数显示 `--` 而不是整条消失，面板标题与说明同步跟随；顺带修掉右侧留白不足（132 → 170px）导致产量接入后标签会被画布裁掉的问题（B-515）；② 单线趋势图补齐与总览一致的观感：每个时间点显示描边圆点（`showSymbol` 不再关掉、`symbolSize` 4 → 5），第二条曲线在「用电量」形态下加渐变面积阴影（「单位电耗」形态不铺，避免糊住产量区域）（B-516）** | 前端 |
| **v1.2.3** | **2026-09-23** | **趋势图可读性与空值渲染：① 年 / 月 / 日三个维度的 X 轴改为时间正序**（最左最旧、最右是当前周期）——原来接口按「新 → 旧」出数，今天落在最左边，且总览按「末项 = 本期」取的「去年」实际是三年前（B-512，顺序在接口层定死，前端不再各自 reverse）；② 修**单线页年/月/日趋势图整张不显示**：`TrendPoint.kwh` 类型声明与接口不一致（接口给 null、类型写 number），`x.kwh.toFixed()` 通过编译却在运行时抛异常把整个 computed 带走；总览同维度则把缺数画成 0，出一条「用量暴跌」的假曲线（B-513，类型改可空 + 出数统一走 `scale()` + `connectNulls:false`）；③ 修**图例单位与实际出数不一致**（图例恒写「万瓶/万kWh」，年维度按亿、时维度按千出数），除数与单位合并为单一来源，并让非零值不被舍成 0（B-514）；④ 趋势图说明补「每柱＝1 年 / 1 个月 / 1 天 / 1 小时」，解决「看不出这张图是每日还是每小时」；⑤ 后台「用量查询」趋势图的产量占位与缺数由 0 改 null，与 ADR-013 对齐；⑥ 补 7 个单元测试锁住序列顺序与空值口径（`apps.energy` 共 44 个）** | 全栈 |
| v1.2.2 | **2026-09-23** | **采集口径与时间维度：① 修复**13 路电表全部读取失败**（pymodbus 3.8 起把 `slave=` 改名 `device_id=`，采集器写死其一，与锁定的 3.7.4 不兼容 —— B-501）；② 采集节奏改为**每小时一次**（整点 + 偏移采样），差值区间严格等于 1 小时；③ 重写差值端点判定：改为「距整点最近且在 ±20 分钟容差内」，**修掉两个会产生假数字的缺陷**（本小时没采到样时拿隔天读数相减得 0 —— B-502；同小时补采把区间拉长到 89 分钟 —— B-503）；④ 「进行中的小时」参考值改为要求两条以上读数，消除每小时节奏下错一个小时的问题（B-504）；⑤ 全部小时行不可信时不再返回 0（B-505）；⑥ 大屏新增**日期切换**（历史模式暂停自动刷新并明确标注），历史日期按「整天已结束」取值（B-507）；⑦ 后台新增**用量查询**页（时间区间 + 时/日/月粒度 + 车间/各线，支持 CSV 导出），与大屏共用 `/api/energy/range` 同源出数；⑧ 修复「演示数据」横幅在所有页面无条件显示（B-506） | 全栈 |
| v1.2.1 | **2026-09-22** | **数据闭环与口径统一：① 后台配置接口打通（`/api/admin/*`）——此前 admin 路由误挂在 `/api/energy/admin/` 下致前端全 404（B-404），列表类接口又因 `JsonResponse safe` 报 500（B-405），修复后后台网关 / 计量点 / 产线 / 数据质量 / 看板全部读真实库，网关与计量点支持**后台增删改 + 连接测试 + 试采**（脱离配置文件维护）；② 大屏切真实接口（`mode='api'`），单线页「产量与用电趋势」补齐**年 / 月 / 日 / 时**四维切换（与总览同口径，读 `eep_period_summary`）；③ 修复**首次进入总览 / 单线页图表空白、必须切走再切回才刷新**（B-401 时序坑 + B-402 容器 0 高）；④ 演示产量占位统一由 0 改为 **null**（`--`），等真实产量接口接入后置 `productionEnabled=true` 即恢复；⑤ 新增 `ingest_production`（正式写入 + null 占位）与 `summarize`（日 / 月 / 年周期总计落表）命令，日月年 KPI 大屏与后台同源** | 全栈 |
| v0.0.1 | 2026-09-21 | 原生 JS 版大屏交付（`frontend/`），双屏 1920×1080、Mock 数据、RTSP 视频降级方案 | 前端 |
| v0.1.0 | 2026-09-22 | 按技术栈文档重构为 pnpm monorepo；大屏迁移到 Vue 3.5；新增后台管理系统（21 页）；补齐 shared 共享层与交付文档集 | 前端 |
| v1.1.0 | 2026-09-22 | ① 接入 go2rtc 浏览器视频监控（四协议自动降级，现场已验证）；② 仓库结构统一为前后端分离（`frontend/` + `backend/` + `deploy/` + `docs/reference/`）；③ 预留后端接口契约（api-contract + OpenAPI 21 接口）；④ 初始化 Git 仓库；⑤ 修复并行构建卡死问题 | 前端 |
| **v1.2.0** | **2026-09-22** | **后端采集侧落地：① 对接 4 台 485 串口服务器 13 路产线电表（Modbus RTU over TCP）与蒸汽平台 API，实测 14/14 点位 1.4 秒采完；② MySQL 建库建表并按「小时差值」口径入库（缺数/换表/尖峰/偏离整点四类异常处理）；③ 不拆线别的**水汽只记车间总表**（只登记不摊派）；④ 预留只读接口 `/api/energy/*`，**产量接口留空（501）**；⑤ 12 个单元测试锁住差值口径；⑥ 水位表接口地址待现场提供** | 后端 |
| v1.1.1 | 2026-09-22 | 修复大屏视频「只有声音、没有画面」：① 根因是视频容器高度被算成 0（B-209）；② 首次播放因 watch 在挂载前触发而静默失败（B-211）；③ 失败记录被递归清空（B-210）；④ 探测时用 `display:none` 导致媒体元素跳过解码（B-212）。另把大屏链路按现场实测定为 **mse → hls → mjpeg**（mse 首选、hls 可自证兜底）并加 V 键切换链路（ADR-008）** | 前端 |


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
| 3 | ~~大屏真实数据接口~~ | `dataConfig.mode` 已为 `'api'` | ✅ **v1.2.1 已完成**：大屏读真实库（电量/汽量为采集数据，产量如实为 null） |
| 4 | 能耗强度参数回填 | `frontend/packages/shared/src/config.ts` → `energyModel` | 必须用真实历史数据，当前为行业经验值 |
| 5 | 告警阈值标定 | 同上 → `thresholds` | 需与能源管理员共同确认 |

#### P1 — 核心业务

| # | 功能 | 接入点 | 说明 |
| :--- | :--- | :--- | :--- |
| 6 | 产线主数据增删改 | `views/production/LineListView.vue` | |
| 7 | ~~计量点位维护与连通性测试~~ | `views/device/MeterView.vue` | ✅ **v1.2.1 已完成**：后台增删改 + 试采真实读数（`POST /api/admin/meters/{id}/test`） |
| 8 | ~~采集网关配置下发~~ | `views/device/GatewayView.vue` | ✅ **v1.2.1 已完成**：后台增删改 + 逐点连接测试（`POST /api/admin/gateways/{id}/test`） |
| 9 | 告警确认与批量确认 | `views/alarm/RealtimeView.vue` → `ack()` | |
| 10 | 告警规则增删改 | `views/alarm/RuleView.vue` | |
| 11 | 用户 / 角色 / 菜单 / 参数 / 字典 CRUD | `views/system/*.vue` | |
| 12 | 报表生成与导出（xlsx / pdf / csv） | `views/energy/ReportView.vue` | |
| 13 | 各类列表导出 | 各页面 `#actions` 中的导出按钮 | |

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
| GET | `/api/energy/overview` | 大屏总览聚合数据（顶层必须含 `workshop`）✅ 已实现 |
| GET | `/api/energy/line/{lineId}` | 单线聚合数据 ✅ 已实现（含 daily30 / monthly12 / yearly5，供单线页年/月/日/时切换） |
| GET | `/api/energy/hourly` | 原始小时明细（导出用）✅ 已实现 |
| GET | `/api/energy/production` | 产量明细 ✅ 已实现（数据源未接入时如实返回 null + available=false） |
| GET | `/api/energy/period-summary` | 日 / 月 / 年总计（读 `eep_period_summary`，大屏与后台同源）✅ 已实现 |
| POST | `/api/auth/login` / `logout` | 认证（正式环境替换为 SSO 回调）⬜ 待实现 |
| GET | `/api/admin/overview` | 后台首页看板 ✅ 已实现 |
| GET | `/api/admin/lines` | 产线主数据 ✅ 已实现 |
| GET | `/api/admin/meters` `gateways` `quality` `collect-logs` | 设备与采集 ✅ 已实现 |
| POST/PUT/DELETE | `/api/admin/gateways[/{id}]` `meters[/{id}]` | 网关 / 计量点后台增删改 ✅ 已实现（写操作置 `source=admin`，`seed_registry` 不覆盖） |
| POST | `/api/admin/gateways/{id}/test` `meters/{id}/test` | 连接测试 / 试采（返回**真实读数**）✅ 已实现 |
| GET | `/api/admin/users` `roles` `menus` `params` `dicts` | 系统管理 ⬜ 待实现（前端暂走演示数据） |
| GET | `/api/admin/alarms` · POST `/api/admin/alarms/{id}/ack` | 告警 ⬜ 待实现（前端暂走演示数据） |
| GET | `/api/admin/logs/operation` `login` | 操作 / 登录日志 ⬜ 待实现（前端暂走演示数据）；采集日志 ✅ 已实现 |
| GET | `/api/admin/reports` | 报表模板 ⬜ 待实现 |

> 按《代码仓库管理规范》要求，**后端 API 必须有 OpenAPI / Swagger 文档**，
> 且所有接口必须鉴权（白名单接口需评审通过）。契约文件已先行交付，
> 后端实现时直接按契约填充即可，前端无需改动。

### 3.4 视频监控链路（已实现，不经后端）

视频走**浏览器直连 go2rtc 网关**，不占用任何 Django 接口：

| 用途 | 地址 | 谁在用 |
| :--- | :--- | :--- |
| HLS | `{gateway}/api/stream.m3u8?src={name}` | **大屏首选**（hls.js + 自有 `<video>`，可校验出画） |
| MJPEG | `{gateway}/api/stream.mjpeg?src={name}` | **大屏兜底**（`<img>` 收到真实帧才算成功） |
| MSE 播放页 | `{gateway}/stream.html?src={name}&mode=mse` | 后台「实时预览」；大屏按 V 切到 mse 链路时启用（画面不可校验） |
| WebRTC 播放页 | `{gateway}/webrtc.html?src={name}` | 同上（画面不可校验） |
| 抓图 | `{gateway}/api/frame.jpeg?src={name}` | 后台缩略图 |
| 流列表（自检） | `{gateway}/api/streams` | 后台「检测网关」 |

> **v1.1.1 起大屏默认不再使用 mse**：mse / webrtc 走跨域 iframe，父页面无法判断里面
> 到底有没有画面（iframe 的 `load` 只代表网页加载成功），一旦「有声无画」会被误判为成功并卡住降级链；
> 且大屏是 `transform: scale()` 缩放舞台，Chromium 对「缩放祖先 + 圆角裁剪 + 跨域 iframe 内视频」
> 存在分层缺陷。详见 B-207 与 §5.5。

> 现场当前只接入 1 路摄像头（`my_camera` → `10.113.1.118` 通道 102），
> 其余 5 条产线在 `videoConfig.channels` 中标记 `enabled: false`，
> 摄像头到位后按 `deploy/go2rtc/README.md` 的「新增一路摄像头」步骤启用即可，
> **前端代码无需改动，只改配置**。

---

## 四、Bug 台账

> 状态取值：`已修复` / `待修复` / `无法复现` / `设计如此`

### 4.0 v1.4.3 修复的 Bug（B-527 ~ B-528）

> 现场反馈：「大屏每天 23 点数据显示后，整体趋势图会固定在前一天的数据，
> 不刷新，必须手动刷新浏览器才更新」。查下去是**两个独立缺陷**：
> 一个是跨零点没人翻页，一个是 v1.4.2 降频留下的半小时空窗。

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B-527** | 过了零点，大屏的趋势图 / KPI / 小时明细**整天停在昨天**，必须手动刷新浏览器才恢复。挂机场景下必然发生（大屏从不刷新） | `stores/energy.ts` 的 `selectedDate` 只在 store 创建时取一次 `isoToday()`，之后再也不重算；而 `isHistory = computed(() => selectedDate.value !== isoToday())` 里 **`new Date()` 不是响应式依赖**，Vue 求值一次就永久缓存。于是零点后 `selectedDate` 仍是昨天 → 接口按「这一天已经过完」把昨天整日返回 → 画面永远停在昨天。**两层是同一种错：把「时间」当常量** | `today` 改为反应式 `ref`；新增 `syncToday()`，定时器每一轮先对表，日期变了就把**没有被用户钉住**的视图推到新的一天（`userPicked` 为真时不拽 —— 夜里回看昨天不该被零点静默改写）；`isHistory` 改比对反应式 `today` | `apps/screen/src/stores/energy.ts` | 已修复 |
| **B-528** | 大屏翻到新一天后的 00:00—00:30，「近 30 日」里**昨天那一根会先空一晚**（00:30 结算完才回来） | v1.4.2 把汇总降为每天 00:30 结算一次，但「昨天」这一行的上一次写入是**前天 00:30**（只有几小时数据）。当期数字已经现算了、昨天却没有兜底，于是刚翻页就把昨天显示成空 —— 正是「23 点数据出来后趋势图出问题」的另一半 | `summary._day_not_settled()`：日级周期里**只有今天与昨天**允许「汇总行缺失或 `covered_count < expected_count` 就退回小时表现算」，更早的周期仍读表（回退一次要扫小时表，限两个周期，`daily30` 的成本不涨）。顺带让 `EEP-Rollup` 没注册 / 跑失败时**退化成现算**而不是给残缺旧值 | `apps/energy/services/summary.py` | 已修复 |

**这一版留下的规矩**

1. **界面里凡是「今天」，都必须是反应式状态**，不能是 `computed(() => new Date())`
   —— 没有响应式依赖的 computed 只求值一次，跨零点后就是错的（B-527 与 DateSwitch
   内部那个 `today` 同因，已一并改成上层传入）。
2. **凡是把某个计算降频到「每天一次」，都要问一句：新的一天开始时，上一次结果是多久前的？**
   答不上来就必须给当期留一条现算的退路（B-528）。

### 4.1 v1.4.1 修复的 Bug（B-522 ~ B-525）

> 现场反馈两条：「大屏和后台的数字对不上（09-27 22 点一线 7.8，大屏 7.3）」、
> 「每天 23 点的数据都有问题，跟前面 22 个小时相差过大」。
> 逐格比对接口后定位到**四处独立成因**，其中 23 点那条最危险：
> 数字量级正常、没有任何报错、还把日累计一起抬高了。

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B-522** | 大屏选任意历史日，**23:00 那一格都大一个数量级**：09-27 一线显示 85 kWh（真值 8.1）、三线 3285（343.1）、五线 3303（330.0），状态还写着「进行中」；一线日累计因此 463 对不上后台 385.5 | 两个缺陷叠在一起：① `_now_for(历史日)` 把 as-of 造成那天 **23:59:59**，而 `hourly_cells` 用 `day == now.date()` 判「是不是今天」—— 查哪天哪天就「是今天」，于是第 23 小时被判成进行中、改走实时窗口；② `current_hour_usage` 的窗口**只写下界**（`collect_time >= 整点`，没有上界），于是把「那天 23:04 之后直到此刻」的全部读数减成一个数（约 10 个小时的用量塞进一格）。「一天是否已经结束」是调用方的意图，无法从时间戳反推 | `hourly_cells` 增加显式 `closed` 参数（关闭日 24 小时全部走聚合表，无进行中、无计划），`build_line` / `build_workshop` / `build_overview` 一路透传，`api` 用 `_is_closed(day)` 判定；`current_hour_usage` 补上界 `collect_time < 整点+1h`；快照新增 `ongoingHour`（历史日为 null）与 `dayClosed`，`currentHour` 退回「排序锚点」单一职责 | `services/aggregate.py`<br>`services/snapshot.py`<br>`api.py`<br>`components/HourTable.vue` | 已修复 |
| **B-523** | 大屏的日 / 月 / 年 KPI 恒比后台区间合计少一截：09-27 一线 377.4 vs 385.5，且 `energy_daily` 的 `covered_count` 在 09-25/26/27 **恒为 23** | `collect --aggregate` 只刷 `summarize_days(now.date())`（今天）。昨天 23 点的差值要等**今天 00:04** 采到跨日边界读数才算得出来，那一刻 `now.date()` 已经是今天 —— 昨天的日行永远停在 23 个小时上。而月 / 年按日行累加（为了「月 = 各日之和」），三个尺度一起偏小；大屏读汇总表、后台读小时表，于是永远差最后一小时 | 抽出 `summary.refresh_rollups(now)` 统一窗口 = **昨天 + 今天** + 近 2 月 + 今年，`collect` 与 `collect_loop` 共用（两条采集路径不能刷出两套数字）；现场已用 `manage.py summarize --dim day --days 10` 回填，回填后日行与小时表逐格相等 | `services/summary.py`<br>`management/commands/collect.py`<br>`management/commands/collect_loop.py` | 已修复 |
| **B-524** | 同一格大屏「25」、后台「24.8」，几乎每行都差 0.x | 大屏 `round_kwh` 对 ≥20 kWh 取整、只对小数值保留 1 位小数，后台表格一直是 1 位小数。取整规则的本意是「刚过整点数值小，取整会让单位电耗在刷新之间跳」，代价变成**两边逐格对不上** —— 现场先怀疑系统错乱，真正的问题反被这种显示差异淹掉 | 电量口径统一为**一律 1 位小数**：后端 `round_kwh` 直接 `round(v, 1)`；前端新增 `dec1()`（整数不带尾随 `.0`），`big()` 在「万」以下对非整数保留 1 位；`HourTable` 去掉 `< 20` 特例，趋势图备注 `toFixed(0)` → `dec1` | `services/snapshot.py`<br>`packages/shared/src/format.ts`<br>`components/HourTable.vue`<br>`views/OverviewView.vue`、`views/LineView.vue` | 已修复 |
| **B-525** | 反馈的「22 点大屏 7.3 / 后台 7.8」：实测两边当前都是 7.8，**而 7.3 恰好是 09-25 同一格的值** | `/api/energy/overview` 单次要 2—4 秒（六条线各自回查聚合表）。切日期时旧快照还在屏上，日期标签已经翻新的一天；连切两次时后发先至的旧响应还会直接覆盖新画面 —— 于是「A 日的数字挂在 B 日的标签下」 | `stores/energy.ts` 加请求序号，过期响应**一律丢弃**；新增 `snapshotDate` 与 `stale`，日期切换期间 `DateSwitch` 徽标写「正在取数 / 切换中」，不给静默错位的机会；历史日的「本小时」类文案改按 `dayClosed` 显示「23 时段」 | `stores/energy.ts`<br>`components/DateSwitch.vue`<br>`components/UtilityPanel.vue` | 已修复 |
| **B-526** | 修 B-522 时把 `utility.rateNow` 由 `0.0` 改成 `null`（查历史日没有「此刻流量」，0 会被读成「现在没流量」），总览页整片渲染不出来 | 只改了后端的可空性，**没同步 `types.ts`**，于是 `it.d.rateNow.toFixed(1)` 通过了类型检查却在运行时抛异常 —— 和 B-521 同一类：子组件渲染抛错会带走整棵子树。控制台里那句 `Unhandled error during execution of render function at <UtilityPanel>` 才是真凭据 | `types.ts` 的 `rateNow` 改 `number \| null`，让 tsc 把所有消费点逼出来，再逐个做空值分支 | `packages/shared/src/types.ts`<br>`components/UtilityPanel.vue` | 已修复 |

**这一版留下的三条硬规矩**

1. **「这一天过完了没有」必须由调用方说出来**，不能靠 as-of 时间戳推断 —— 查历史日时 `now` 是造的，任何 `day == now.date()` 的判断都会把它当成今天。
2. **实时 / 参考值窗口必须有上下界**。只写下界的查询在真实时钟下无害、在造的时钟下必然越界。
3. **接口把一个字段改成可空，第一步是改 `types.ts`**，让编译器列出全部消费点；直接改后端等于埋一颗运行时炸点（B-521 / B-526 两次同因）。

**新增能力**：无（纯缺陷修复版）。接口新增 `ongoingHour` / `dayClosed` 两个只读字段，旧字段语义不变。

### 4.2 v1.4.0 修复的 Bug 与新增能力（B-519 ~ B-521）

> 这一版是功能迭代（后台四个模块），顺带修掉三条缺陷。现场反馈的「汽计量界面无法进行查询」
> 查下去发现是**三个计量页共同的缺陷**，而且第 3 条的病因完全不在报错的位置。

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B-519** | 后台「汽计量」页的明细表恒显示「暂无数据」，点「查询」也没反应；实测**电、水两页同样为空** | 三页的明细行都由 `onMounted` 里那个未 await 的快照换算而来，而 `TablePage` 只在**自己挂载时**取一次数 —— 那一刻快照还没回来，`rows` 是空数组，之后没有任何机制再取一次。「查询」按钮也只是拿同一份空数组重跑 | 三页取数统一改走 `/api/energy/range`（与用量查询页、大屏同源），并把「数据是否已就绪」并入 `extraQuery`（`TablePage` 已 watch 它自动回第一页重取）；水/汽两页合并成一个 `UtilityUsageView`（只差能源类型），不再各维护一份逻辑 | `apps/admin/src/utils/energyUsage.ts`<br>`views/energy/ElectricView.vue`<br>`views/energy/UtilityUsageView.vue` | 已修复 |
| **B-520** | 水 / 汽页下方明细表右侧空出一大片，表头看起来「分布不均」 | 四列全用固定 `width`（合计 570px），容器更宽时 el-table **不会把剩余空间分给固定列**，于是右边留白 | 明细列一律改 `min-width`，让列按比例铺满；用量查询页同样改法 | `apps/admin/src/views/energy/*.vue` | 已修复 |
| **B-521** | 用量查询页的明细表**一列都渲染不出来**（只剩表头占位，`th`/`td` 数量为 0），看起来是表格组件的问题 | 真因在同页上方的 `ChartPanel`：新写的 `energyUsageOption` 把 `yAxisIndex` 算成 `i === 0 ? 0 : i + 1`，第二条序列指向了**不存在的 yAxis**，ECharts 在 `setOption` 时抛 `yAxis "0" not found`。这个异常出在 **mounted 钩子**里，**会中断整棵子树的挂载** —— 于是排在图表后面的 el-table 一个列都没注册上。症状在下游，病因在上游 | 序列与轴一一对应（第 i 条用第 i 根轴）；`energyCumulativeOption` 里同类问题（`showOutput ? 2 : 1`）一并改掉；补 7 个图表配置的离屏渲染冒烟测试 | `apps/admin/src/utils/charts.ts` | 已修复 |

**v1.4.0 新增能力（按需求四条）**

| # | 能力 | 落点 |
| :--- | :--- | :--- |
| 1 | 数据中心加**起止时间筛选（年-月-日 时 → 年-月-日 时）**，整页数字随区间出 | `DashboardView.vue` + `GET /api/energy/dashboard` |
| 2 | 主趋势图改为**单箱变动费用**（叠加「每月标准变动费用」水平虚线）；三张**按能源分**的累计趋势图（电为主、水汽为辅，每张含累计用量 / 累计产量 / 累计单耗）；删除「今日用量统计」改为**区间累计用量** | `DashboardView.vue`、`utils/charts.ts` 的 `costPerCaseOption` / `energyCumulativeOption` |
| 3 | 用量查询：标题改**区间能源趋势图**、**电/水/汽曲线多选**、车间合计含水汽、**线别筛选**（选单线时水汽勾选禁用并说明「无独立表」）；三个计量页同样加时间 + 线别筛选 | `UsageQueryView.vue`、`energyUsage.ts` |
| 4 | 设备与采集新增**仪表读数查询**：按时间 / 网关 / 仪表查 `energy_raw` 原始累计读数（含倍率后数值、成功失败、原始报文），分页 + CSV 导出 | `ReadingQueryView.vue` + `GET /api/admin/readings` |
| 附 | 成本算料落地：新表 `system_param`（电价 / 水价 / 汽价 / 箱规 / 每月标准变动费用）+ `seed_params` 命令 + `GET/PUT /api/admin/params`，后台「系统参数」页从演示数据切为**真实读写** | `models.py`、`services/cost.py`、`ParamView.vue` |

> **口径三条，别改回去**：
> ① 任一单价或箱规未标定 → 变动费用整段 `null`，**不按 0 计入**（少算一项会得到偏小但看起来正常的费用）；
> ② 产量未接入 → 累计产量 / 累计单耗 / 单箱费用一律 `null`，界面显示 `--` 并把 `cost.blockers` 的原因摊开写出来；
> ③ 标准变动费用为 `null` 时**不画那条基准虚线**（画成 0 等于宣称「标准是 0 元/箱」）。
> 后端补 13 个单元测试锁住以上口径（`apps.energy` 共 57 个）。

### 4.3 v1.3.1 修复的 Bug（B-517 ~ B-518）

> 现场反馈两条：「总览底部六线对比的底部没对齐，影响美观」「小时数据异常之后应该显示异常，
> 而不是都 10 点了 8 点那行还写着进行中」。第二条是真问题，而且**根源在类型没把后端已有的信息接住**。

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B-517** | 总览中列底部的「六条产线用电量对比」面板底边比左右两列高约 24 舞台 px，三列底边参差不齐 | `PanelBox` 只有在**不传 `height`** 时才加 `panel--grow`（`flex: 1 1 auto`）。左列「车间水耗/汽耗」与右列「数据与阈值告警」都没传 height → 自适应撑满；中列末块写死了 `height="var(--c3)"` → 固定 276px，撑不满列高 | 去掉该面板的固定高度与已无用的 `--c3` 变量，让它与左右两列一样 `grow` 到底。修后实测三列末块面板底边同为 `420.31`，画布由 958×206 变 958×244，内容纵向铺到 y=239 | `apps/screen/src/views/OverviewView.vue` | 已修复 |
| **B-518** | 单线页「本线小时明细」里，早已结束的异常整点显示**「进行中」**（实测 A5 线 08:00 / 09:00，电量 23 / 26 kWh，明显低于相邻的 49—57 kWh）。现场以为「等一会儿就补上了」，其实那一小时永远不会再变 | 后端 `HourCell.partial` 的含义是「**本小时合计不完整**」，两种成因共用一个布尔：① 当前小时还没跑完；② 已结束的整点里有表缺数 / 端点偏离整点 / 尖峰被排除。后端**一直在响应里带 `quality`**，但 `HourPoint` 类型没声明这个字段，前端只能拿 `partial` 猜状态 → 一律渲染成「进行中」 | `HourPoint` 补声明 `quality`（并把 `partial` 的注释改成「合计不完整，≠ 进行中」）；状态判定改为**进行中只看 `h === currentHour`**，已结束的整点按 `quality` 显示 `异常·缺数 / 异常·换表 / 异常·尖峰 / 异常·部分缺数`；文案集中在 `HOUR_QUALITY_LABEL` 单一来源，「今日分时」图的 tooltip 同步改用这套判定；mock 引擎补齐 `quality`，保证演示模式与真接口状态列一致 | `packages/shared/src/types.ts`<br>`packages/shared/src/format.ts`<br>`packages/shared/src/mock/energy.ts`<br>`apps/screen/src/components/HourTable.vue`<br>`apps/screen/src/charts/index.ts` | 已修复 |

> **B-518 与 B-513 / B-509 是同一类错误的第三次现身**：后端已经在响应里给了正确的信息
> （`quality`），是**前端类型没声明**，于是只能拿一个语义更宽的字段去猜。
> 猜错的表现不是报错，而是「一个看起来合理的错状态」。
> 规则：**接口字段只要在响应里出现，就要进类型**；语义会重叠的两个概念
> （「没结束」与「不完整」）必须分成两个字段，不能靠一个布尔扛两种意思。

### 4.4 v1.3.0 迁移期间发现的库表问题（B-6xx）

> 在真实库上跑迁移时撞出来的 —— 都是**旧结构本身的缺陷**，新设计正好把它们挡住。
> 完整实录见 `backend/docs/数据库设计-v1.3.0.md` §八。

| ID | 现象 | 根因 | 处理 |
| :--- | :--- | :--- | :--- |
| B-601 | 删旧汇总表的 `line` 列时报 `Duplicate entry 'line-day-2026-09-22' for key 'uniq_period_summary'` | MySQL 删列会**重建唯一索引**；旧唯一键 `(scope, line, dim, period_key)` 里 6 条产线只在 `line` 上不同，删掉后三元组互相重复 | 该表在迁移末尾会被删除，改为**迁移第一步先清空它** |
| B-602 | 给新表加唯一键时报 `Duplicate entry '14-2026-09-22 17:18:00' for key 'uk_raw_meter_time'` | **旧 `eep_reading` 从一开始就没有唯一约束** —— 库里确实存在同一秒的重复读数（meter_id=14 在 2026-09-22 17:18:00 两条）。重复读数会让「点数齐全」的判定失真 | 加约束前按 `(meter_id, collect_time)` 去重，保留 `id` 最大的一条 |
| B-603 | 回填 SQL 报 `Unknown column 'line_type'` / `x.line_id` | 操作顺序错了：回填用到了后面才 `AddField` 的列 | 回填之前先加列（产线三列 + `raw`/`hourly` 的 `line`），顺序固化进 0006 并在文件末尾写明 |

**由此固化的三条迁移顺序（写进代码注释，别再踩）**

1. **旧索引 / 旧唯一约束的删除必须早于字段改名** —— MySQL 删索引要按字段名定位，
   字段被 `RenameField` 改掉后直接 `KeyError`（`date` / `kind` / `at`）。
2. **新增的必填外键一律「先可空 → 回填 → 改非空」**（0006 可空 + 回填，0007 收紧）；
   直接非空写不进已有行，给默认值等于凭空造数据。
3. **回填之前，它要写的每一列都必须已经存在** —— 顺序写错会在 MySQL 层报一个看不出因果的错。



### 4.5 v1.2.4 修复的 Bug（B-515 ~ B-516）

> 这一轮是纯前端观感：现场反馈「总览下方的产线对比没对齐界面边界」「单线趋势图要和总览一样有圆点和阴影」。
> 查下去发现第一条不是对齐问题，而是**整块图基本没画出来**。

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B-515** | 总览下方「六条产线产量与单位电耗对比」面板看起来是空的：958px 宽的画布只有最左边约 40px 有线名，右侧一大片空白，被读成「图没对齐边界」 | 条长取的是**今日产量**，而产量未接入 → 六条全为 `null`。ECharts 对 null 值既不画柱，`position: 'right'` 的标签也**随之消失**，于是柱、标签、数值一个都不剩。顺带另一处：`grid.right` 只留 132px，而最长标签「52.75 万瓶 · 7.71 kWh/千瓶」约 155px —— 等产量接入后右侧文字会被画布右边缘**裁掉** | 改成与单线趋势图同一套**自适应口径**（ADR-013）：产量已接入 → 条长＝产量 + 右侧单位电耗；产量未接入 → 条长＝**今日用电量** + 右侧「用电量 · 占车间合计%」（真实采集值，不是空面板）。缺数一律显示 `--` 而不是整条消失；右侧留白 132 → 170px；面板标题与说明文字跟着口径变（「六条产线用电量对比」） | `apps/screen/src/charts/index.ts`<br>`apps/screen/src/views/OverviewView.vue` | 已修复 |
| **B-516** | 单线趋势图的曲线**没有圆点、下方也没有阴影**，与总览观感不一致，多个时间段难以分辨 | `lineTrendOption` 两条曲线都写了 `showSymbol: false`（总览那条没写），第二条曲线也**完全没有 `areaStyle`** | 去掉 `showSymbol: false`，`symbolSize` 4 → 5 并补 `borderColor: '#0a0d3a' + borderWidth: 1.5`（与总览同款描边圆点）；第二条曲线在「用电量」形态下补 `hexA(P.amber, 0.22) → 0` 的渐变面积。**「单位电耗」形态刻意不铺面积** —— 它是虚线参考线，再填一层会把产量曲线下方糊住 | `apps/screen/src/charts/index.ts` | 已修复 |

> **B-515 的教训**：「没对齐边界」这类观感反馈，根因往往是**这块压根没画出来**。
> 别先调 margin —— 先量一下画布上有墨的像素横跨到哪（本次实测 `inkX = [4, 40] / 958`，一句话定位）。
> 另外，凡是「某个数据源未接入就整块留空」的面板，都要问一句：**这块图有没有不依赖那个数据源的画法？**

### 4.6 v1.2.3 修复的 Bug（B-512 ~ B-514）

> 这一轮全部出在**趋势图**上，现场反馈的原话是「各个单线根本没有自己的趋势图」。
> 三条是同一条链上的：接口顺序反了 → 图读不顺；接口给 null 而类型说不是 null → 图直接不渲染；
> 单位文案与出数除数各写一处 → 图例和 tooltip 互相打脸。

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B-512** | 总览与单线的「年 / 月 / 日」趋势图**当前日期在最左边**，越往左越新，看的人要把图反过来读；顺带「本年较上年」比的是**三年前** | `summary.recent_days / recent_months / recent_years` 生成的是「新 → 旧」，而 ECharts 的 category 轴按数组顺序从左向右铺开。前端另一处（总览取 `yearly5[length-2]` 当去年）却按「末项 = 本期」写 —— 两种假设同时存在，谁都没错，合起来就错 | 三个函数一律改为**时间正序**（末项 = 本期），顺序在**接口层**定死，前端不再各自 reverse；补 7 个单元测试锁住顺序（含跨年、跨月边界） | `backend/apps/energy/services/summary.py`<br>`backend/apps/energy/tests.py` | 已修复 |
| **B-513** | 单线页「年 / 月 / 日」三个维度的趋势图**整张画不出来**：面板框、标题、图例都在，只有图是空的；总览同维度则画出一条「用量暴跌到零」的假曲线 | 接口在「该周期没有任何可信小时差值」时 `kwh` 给 `null`（口径本身没错），但 `types.ts` 把 `TrendPoint.kwh` 声明成了 `number`。于是 `x.kwh.toFixed(0)` **能通过类型检查**、在运行时抛 `TypeError`，把整个 computed 带走；总览那条写成 `(x.kwh / 1e4).toFixed(3)`，`null` 参加除法被当成 0，于是缺数被画成「这个周期没用电」。**越早上线、缺数周期越多，越必现** | `TrendPoint.kwh` 改为 `number \| null`，让类型与接口一致（此后编译器强制每个取值点处理空值）；出数统一走 `scale()`，缺数原样传 `null`（柱留空、线断开），并补 `connectNulls: false` 禁止拿两端凑中间没采集的那段 | `packages/shared/src/types.ts`<br>`apps/screen/src/charts/index.ts`<br>`apps/admin/src/utils/charts.ts` | 已修复 |
| **B-514** | 总览趋势图的图例永远写「产量（万瓶）/ 用电量（万kWh）」，可「年」维度实际按**亿**出数、「时」维度按**千**出数 —— 图例与 tooltip 互相打脸；同时小值在固定单位下被舍成 0（单线一年 23 kWh，在「万kWh」轴上显示为 `0`，读作「这一年没用电」） | 除数写在 `charts/index.ts`、单位文案写在模板 `<span>` 里，两处各写一套、改一处忘一处；`toFixed(2)` 对小于 0.005 的值一律给 0 | 除数与单位合并成 `OVERVIEW_TREND` / `LINE_TREND` **单一来源**，图表与图例都读它（`LineView` 原来另写的一份 `TREND_UNITS` 一并删掉）；`scale()` 在非零值被舍成 0 时自动加精度 | `apps/screen/src/charts/index.ts`<br>`apps/screen/src/views/OverviewView.vue`<br>`apps/screen/src/views/LineView.vue` | 已修复 |

> **B-513 的教训值得单独记**：接口早就在返回 `null`，是**类型声明撒了谎**，
> 于是 TypeScript 一路放行到运行时才炸。这类 Bug 的症状还不是「报错」，
> 而是「这块图是空的」—— 现场既不知道少了什么，也不知道为什么少。
> 凡是接口可空的字段，类型里就必须写可空；宁可让编译器吵一遍，
> 也不要让页面安静地少一块图。（与 B-509「字段改可空漏改序列化层」是同一件事的两端。）

顺带同口径修正：后台「用量查询」页的趋势图此前把未接入的产量写成 `0`、把缺数小时写成 `0`，
现统一为 `null`（图上不画柱、tooltip 显示 `--`），与 ADR-013 一致。

### 4.7 v1.2.2 修复的 Bug（B-5xx）

> 本版的主线是「**让每个数字都站得住**」。前七条都是同一类问题：
> 口径缺陷或版本不兼容导致**看起来正常、实际错误**的数字 —— 这类错最难发现，也最伤信任。

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B-501** | **13 路电表全部读取失败**，报 `read_holding_registers() got an unexpected keyword argument 'device_id'` | pymodbus 3.8 起把「从站号」参数由 `slave=` **改名为** `device_id=`（`slave` 保留为别名）；采集器写死了 `device_id`，而 `requirements.txt` 锁的是 3.7.4，3.7.4 只认 `slave=`。报错措辞像协议不兼容，现场第一反应是去查 485 接线和从站号 —— 白跑一趟 | 按**实际函数签名**探测该用哪个参数名（`inspect.signature`），不绑定版本；顺带把报文里误导性的 `device_id=` 前缀改回 `slave=` | `backend/apps/energy/collectors/modbus_rtu.py` | 已修复 |
| **B-502** | 某些小时显示 **0 kWh**，看起来像「这一小时没用电」 | 端点取法只写了上界 `at <= 整点`、**没写下界**。本小时根本没采到样时，它会一路往前找到几小时前甚至隔天的读数来相减；两条陈旧读数相减通常正好得 0，且不触发任何异常分支、只被标成 partial。**数字量级正常、没有报错、没有告警**，是最难发现的一类错 | 端点改为「**距整点最近且在 ±20 分钟容差内**」的读数；超出容差直接判**缺数（usage=None）**，界面显示 `--`。宁可让人看见「这里没数」，也不给来源不明的数 | `backend/apps/energy/services/aggregate.py` | 已修复 |
| B-503 | 某两个相邻小时**都没有数据**，或某小时数值明显偏大/偏小 | 同一采样槽内的失败重试会产生多条读数，而端点取的是「下个整点之前的最后一条」，于是 09:01→10:30 这 89 分钟被当成「09 点这一小时」：09 点多算、10 点少算；两条又都因偏离 1 小时被判 partial 而不进合计 —— 现场只看到「这几小时没数据」，想不到是补采造成的 | 端点改为取「**距整点最近**」的一条：采样槽内第一条永远胜出，补采被自然忽略。另加区间长度校验（偏离 1 小时超 5 分钟即标 partial） | 同上 | 已修复 |
| B-504 | 每小时采样节奏下，「当前小时」显示的是**上一个小时**的完整用量 | 「进行中的小时」参考值原来是「上小时末读数 → 本小时最新读数」。每晚采一次之后，本小时唯一那条读数就是整点边界读数（已代表上小时结束），减出来正好是上一小时的用量。错整整一个小时，且量级完全正常 | 要求**本小时内至少两条读数**才算参考值；不足则返回空，界面显示「待整点采集」 | 同上 | 已修复 |
| B-505 | 某小时里所有电表都不可信时，合计显示 **0 kWh** | `sum(空列表) = 0`，代码只判断了「有没有行」，没判断「有没有**可用**的行」 | 无可信行时合计置 **None**（不是 0），质量标 partial | `services/snapshot.py`<br>`services/ranges.py` | 已修复 |
| B-506 | 后台**每个页面**都挂着「本页显示的是内置演示数据」的黄色横幅，包括已经接真实库的页面 | `useSourceBanner()` 返回普通对象 `{ isRemote, show, text }`，而 **Vue 模板只对顶层 ref 自动解包、不解包普通对象里的嵌套 ref**，于是模板里的 `banner.show` 拿到的是 computed 对象本身 —— 恒为真 | 返回值改用 `reactive()` 包装（顶层 ref 会被解包） | `apps/admin/src/utils/source.ts` | 已修复 |
| B-507 | 上午查看昨天的数据时，昨天下午到深夜的小时**全部空白**，像数据丢了 | 历史日期构造「现在」时沿用了当天当前的时分（`combine(day, now.time())`），于是昨天 11—23 点被当成「还没到」，渲染成虚线计划柱 | 历史日期按「**整天已结束**」处理（当天 23:59:59），未来日期按当天 00:00 处理 | `backend/apps/energy/api.py` | 已修复 |
| B-508 | 配置里的 `collect.hourly_offset_seconds`（整点后 60 秒采样）**形同虚设** —— 计划任务若在整点触发，就会在 `H:00:00` 整开采，把刻意留出的避让窗口绕过去 | `--if-due` 的幂等守卫只看「本小时有没有读数」，**没有调用 `slots.due_slot()`**，等于完全忽略采样时刻；而避让窗口的存在理由正是「避开整点瞬间的采集竞争与表内累计刷新」 | `_is_due()` 先问 `slots.due_slot()`：没到采样时刻一律跳过，再判读数是否采全。补 5 个单元测试锁住守卫行为（含「没到点不采」「上一小时读数顶不了本小时」） | `backend/apps/energy/management/commands/collect.py`<br>`backend/apps/energy/tests.py` | 已修复 |
| **B-509** | 查「日/月/年总计」时接口 **HTTP 500**（`/api/energy/period-summary`），页面拿不到任何数据 | 上一轮把 `PeriodSummary` 的数值字段改成可空（该周期无可信数据时写 NULL、绝不补 0），**漏改了接口序列化层**：`api.py` 里仍是裸的 `float(r.kwh)`，`float(None)` 抛 TypeError。而「这个周期没有数据」恰恰是最常见的正常情况 —— 也就是这个接口**只要碰到空数据就 500** | 序列化改为直接把 Decimal / None 交给 `EepJSONEncoder`（Decimal → float、None → JSON null），不再手写 `float()`；新增 4 个接口层回归测试（空值行必须返回 200 + null） | `backend/apps/energy/api.py`<br>`backend/apps/energy/tests.py` | 已修复 |
| **B-510** | 大屏六条产线**全部显示「停线」**，而现场明明在用电（实测各线 6—44 kWh/h） | 状态判定取的是**当前小时**的用电量；每小时采样节奏下，当前小时的值要等下一个整点的边界采样做差才算得出来（见 `slots.py`）→ 恒为 0 → 全部判「停线」。**口径没错、取值时机错**，且整点后的整整一个小时都是错的 | 改取「最近一个已结束且有值的小时」；补 4 个状态判定回归测试 | `backend/apps/energy/services/snapshot.py`<br>`backend/apps/energy/tests.py` | 已修复 |
| B-511 | 采集整体停摆时**界面毫无提示**，大屏安静地显示冻结的旧值，现场只会觉得「今天的数怎么不动了」 | 现成的点位失败告警读的是 `meter.last_ok`，而采集中断时它仍停在上次成功时的 `True`；没有任何规则去看「最新读数距今多久」 | 新增停摆告警：最新读数超过 `collect.stale_alert_hours`（默认 2 小时）未更新，就在车间告警里挂出并直接提示去查计划任务；完全没有采集数据时另给一条提示。补 3 个测试 | `backend/apps/energy/services/snapshot.py`<br>`backend/config/settings.example.yaml` | 已修复 |

> **B-502 值得单独记一笔**：它产出的错值有两个特征 —— 量级正常、无异常标记。
> 项目铁律里有「缺数绝不补 0」，说的是**不要造数**；
> 这一条补充的是「**不要拿不相关的读数凑一个数**」，两者本质相同。
> 判断口径是否可信，可以问一句：这个数如果错了，现场能不能看出来？
> 看不出来的，就要在代码里拦住。

### 4.8 v1.2.1 修复的 Bug（B-4xx）

> 本轮是「数据闭环」迭代：大屏切真实接口、后台打通真实库、演示口径统一 null。
> 其中 B-404 / B-405 是「后台与大屏数据不一致」的直接根源 —— 后台压根没连上真实库。

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B-401** | **第一次进入总览 / 单线页，中列图表空白**，必须切到别的产线再切回来才显示 | 父组件用 `watch(..., { immediate: true })` 喂数据，`immediate` 回调在 **setup 阶段**执行，此时模板 ref 仍是 `null`，`echarts.init()` 无从下手，**option 被静默丢掉**；只有 option 下次变化（切产线 / 切维度）才补渲染 | `useECharts` 改两段式：**挂载前把 option 记进 pending，onMounted 里立刻补渲染**；任何调用方都不必关心自己是不是在挂载前调用 | `screen/src/composables/useEcharts.ts`、`ChartBox.vue` | 已修复 |
| **B-402** | 四处图表容器**塌成 0 高**，ECharts 不报错、页面只表现成「这块图是空的」，与 B-209 视频面板同根 | 所在 `.panel__body` 漏了 `--col`（成了块级），`.chart` 的 `flex: 1 1 auto` 在块级父元素里失效且无显式高度 | ① 补齐漏传的 `body-class`；② `.chart` 加 **`min-height: 96px` 护栏**：万一再漏传，图「矮」而不是「消失」，一眼能看出来 | `screen/src/styles/app.scss` 等 | 已修复 |
| **B-403** | 系统当天才上线 / 采集中断恢复后，「今日累计」要么整片 `--`（被当成系统坏了），要么只显示一小段（被读成「今天几乎没用电」） | 已结束周期按严格口径缺数即 null；进行中周期又没有标注「只统计了部分小时」，两种极端都不符合事实 | 进行中周期给**部分合计 + 覆盖度**（`kwhCoveredHours / kwhExpectedHours`），界面显示「已采集 N/M 小时」 | `backend snapshot.py`、`EnergySummary.vue` | 已修复 |
| **B-404** | **后台所有真实接口 404**（`/api/admin/overview`、`/api/admin/gateways`…），后台页面与大屏数据对不上 | admin 路由误挂在 `include("apps.energy.urls")` 的 `/api/energy/` 前缀下（实际路径成了 `/api/energy/admin/…`），而前端按契约调用 `/api/admin/*` | 路由**拆分导出**：`energy_urlpatterns → /api/energy/`、`admin_urlpatterns → /api/admin/`，旧地址兼容保留；`/api/admin/gateways/<id>/test`、`meters/<id>/test`（试采真实读数）随之打通 | `backend/apps/energy/urls.py`、`backend/eep/urls.py` | 已修复 |
| **B-405** | `GET /api/admin/gateways`、`/api/admin/quality` 返回 **500**：`In order to allow non-dict objects to be serialized set the safe parameter to False` | 这两个接口返回的是**数组**，而 `json_ok` 里的 `JsonResponse` 默认 `safe=True` 只允许 dict | `json_ok` 统一 `safe=False`（本项目所有接口的返回体都受控，不存在「把任意对象序列化出去」的风险） | `backend/apps/energy/api.py` | 已修复 |
| **B-406** | 演示期间产量位显示 **0**（「0.0 万瓶 / 0 千瓶/h」），易被读成「产线停产 / 达产率 0%」 | 产量侧未接入时占位写的是 0；但 0 表示「确实没产出」，null 才是「还没接数据」，两者语义不同 | 口径统一：库内占位行 `output=NULL`（`ingest_production --demo-null`），接口对非真实数据源一律返回 **null**，界面统一显示 `--` 并挂「产量未接入」标注；真实数据源接入后自动恢复数值口径 | `backend snapshot.py / api.py / ingest_production.py`、`shared mock/energy.ts / types.ts`、大屏图表与 KPI | 已修复 |
| B-407 | 单线页控制台警告 `Extraneous non-props attributes (lineId)` | 路由记录配了 `props: true`，但 `LineView` 未声明该 prop，参数以普通 attribute 落到根元素 | 去掉 `props: true`（组件本就直接读 `route.params`） | `screen/src/router/index.ts` | 已修复 |

### 4.9 v1.2.0 后端采集侧的 Bug（B-3xx）

> v1.2.0 接入现场网关（4 台 485 串口服务器 + 1 个平台 API），这些是采集侧的坑。
> 详细说明见 `backend/docs/采集说明.md` §七。

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B-301** | 13 块电表**全部**返回 `No Response received from the remote slave`，像现场 485 断线 / 从站号写错 | 采集器给 `ModbusTcpClient` 传了 `retries=0`。pymodbus 3.7 在这个值下**根本不把请求发出去**（实测 `retries>=1` 才正常；从站不存在时正好耗时一个 timeout） | 改为 `retries=1`，并在代码与文档里写明**不要传 0** | `apps/energy/collectors/modbus_rtu.py` | 已修复 |
| **B-302** | 某小时有一块表缺数时，产线合计**偏小而看起来正常** | 聚合按「有几块表算几块」求和，缺表的那部分被静默扣掉 | 改为「**合计宁可空白，不给偏小值**」：任一表 missing/reset，该小时合计即 `null` | `apps/energy/services/snapshot.py` | 已修复 |
| B-303 | 刚部署后当前小时的参考值带 `progress=0.69`，看着像「41 分钟只用了 0.8 kWh」 | 当前小时聚合把 `progress` 写成了「本小时已过时间占比」，而实际基线是本小时第一条读数（只覆盖几分钟） | `progress` 改取各表实际覆盖占比 | `apps/energy/services/snapshot.py` | 已修复 |
| B-304 | 接口把「没采到」的水汽/电量返回成 `0` | 快照组装时用了 `round_kwh(x) or 0` 兜底 —— 正是项目铁律禁止的「补 0」 | 全部改为 `null`（未知 ≠ 0），并在契约里标注前端需放宽类型 | `apps/energy/services/snapshot.py` | 已修复 |
| B-305 | 采集器首个版本 `retries=0` 之外，还发现「上小时末读数」缺失时当前小时算不出来（刚上线必然发生） | 当前小时需要「上小时末读数」当基线，系统刚部署时没有 | 兜底用**本小时第一条读数**作基线，并如实标注覆盖占比（见 B-303） | `apps/energy/services/aggregate.py` | 已修复 |

### 4.10 v1.1.1 修复的 Bug

> 现象：go2rtc 正常运行、后台「视频监控」预览正常，但**大屏只有摄像头声音、没有画面**。
> 下面 4 条是同一次排查里挖出来的，**B-209 是直接原因**，其余 3 条是「查不到原因」的原因。

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **B-209** | 大屏视频面板**只有声音、没有画面**；面板区域看上去是一片空白 | **视频容器高度被算成 0**。`.panel__body` 默认是块级元素（只有显式加 `panel__body--col` 才是 flex 列），而 `.video` 只写了 `flex: 1 1 auto` 且子元素全是绝对定位 → 容器塌成 0 高。实测面板 `362×0`；iframe 里的 go2rtc 播放页同样 0 高，**音频不依赖尺寸照样出声，画面却没有可绘制的区域** | ① `PanelBox` 传 `body-class="panel__body--col"`；② `.video` 补 `height: 100%` 兜底（块级父元素下也能撑满）；修改后实测 `362×177` | `frontend/apps/screen/src/views/LineView.vue`<br>`frontend/apps/screen/src/styles/app.scss` | 已修复 |
| B-210 | 占位里始终**看不到「失败记录」**，现场只知道「所有协议均无法播放」 | `play(index)` 每次降级都递归调用自己，而函数开头无条件 `tryLog.value = []`，等于每换一个协议就把上一个协议的失败原因抹掉，到头来一条不剩 | 只在最外层（`index === 0`）清空 | `VideoMonitor.vue` | 已修复 |
| **B-211** | **首次进入单线页必然播放失败**（连占位原因都为空）；切到别的产线再切回来反而正常 | `watch(..., { immediate: true })` 在 **setup 阶段**同步触发，那时模板尚未挂载，`boxEl / videoEl / imgEl` 全是 `null`；而原代码写成 `if (!box) return resolve(false)` —— **静默失败**，所以既播不出来也查不出原因 | ① 首播移到 `onMounted`，watch 去掉 `immediate`；② 所有退出路径一律经 `fail()` 记录原因，不再有静默分支 | `VideoMonitor.vue` | 已修复 |
| B-212 | hls 探测总是走到超时才降级 | 媒体元素用 `display: none` 隐藏，而**浏览器会跳过隐藏媒体元素的首帧解码** —— 探测逻辑要等 `loadeddata`，却先把标的物藏了起来 | 媒体元素改用 `opacity` + `.is-on` 类控制显隐（层级：媒体 1 < 占位 2 < 角标 3），元素始终参与渲染 | `VideoMonitor.vue`<br>`styles/app.scss` | 已修复 |
| B-213 | 网络不通时只报「video 解码失败」，看不出是**根本没连上网关** | `hls.loadSource()` 写在 `hls.on(ERROR)` **之前**：清单请求失败得极快（被浏览器拦截时是瞬时失败），错误事件早于监听注册就丢了；只剩媒体元素的 error 兜底，于是原因被写成「解码失败」 | 监听一律先于 `loadSource` / `load` 注册；报错文案补充 `MediaError code`（1 中断 / 2 网络 / 3 解码 / 4 不支持） | `VideoMonitor.vue` | 已修复 |
| B-214 | 画面出来了，但**视频右边挂了一条滚动条**，很难看 | go2rtc 内置播放页在 362×177 这种窄容器里内容溢出，iframe 内部文档就画出了滚动条。跨域 iframe **注入不了样式、删不掉它**。实测（Chromium 153）：不加限制时内部可用宽只有 347（滚动条占 15px） | 给 iframe 加 **`scrolling="no"`**（实测仍被 Chromium 支持）：内部可用宽恢复 362，滚动条消失，**画面不做任何缩放或裁切**。备选方案（放大 `calc(100% + 18px)` 让父级裁掉滚动条）已实测可行但会放大画面，仅在遇到忽略 `scrolling` 的浏览器时启用 | `VideoMonitor.vue`<br>`styles/app.scss` | 已修复 |
| B-215 | 现场确认 MSE 画面正常后，默认链路却还是 hls，每次都要手动按 V | v1.1.1 前半段为规避「不可校验」把默认改成了 hls；现场实测证明 0 高容器才是根因，MSE 本身没问题 | 默认链路定为 **`mse → hls → mjpeg`**：MSE 首选（延迟最低、网关不转码），紧接一个能自证的 hls 兜底（ADR-008 相应改写）；角标去掉琥珀色问号，只留悬停提示 | `VideoMonitor.vue`<br>`config.ts` | 已修复 |
| B-207 | ——（可靠性改进，非本次故障根因） | 原链路首选 `mse`，由 go2rtc 内置播放页承载在**跨域 iframe** 里，父页面只能拿到 `iframe.onload`：只能证明「网页加载成功」，不能证明「有画面」。一旦「有声无画」会被判为播放成功并卡死在第一档，后面的 hls / mjpeg 永远轮不到 —— **这次就是被它挡住了视线** | 大屏默认链路定为 **`mse → hls → mjpeg`**（v1.1.1 现场实测后调整：mse 延迟最低、网关不转码，且大屏画面正常；**紧接一个能自证有画面的 hls 兜底**，见 ADR-008）。另对 iframe 加 `transform: translateZ(0)`（缩放舞台 + 跨域 iframe 的预防措施） | `VideoMonitor.vue`<br>`styles/app.scss`<br>`frontend/packages/shared/src/config.ts` | 已完成 |
| B-208 | 组件里调用 `toast()` 无任何提示；只有 `App.vue` 自己的提示能显示 | `useToast()` 每次调用都**新建一组 ref**，而真正负责渲染提示的是 `App.vue` 持有的那一组，两者互不相干 | 把提示状态提到模块作用域，改为共享单例 | `frontend/apps/screen/src/composables/useClock.ts` | 已修复 |

> **B-209 的排查路径（留档，别再走一遍）**：
> 1. `curl` 网关 `/api/streams` 正常 → 排除网关、摄像头、流名；
> 2. 后台预览正常、HLS 主列表返回 `CODECS="avc1.4D0029"`（H.264 Main）→ 排除编码、CORS、`mode=mse` 参数；
> 3. 用无头 Chromium 打开大屏单线页实测，读到面板尺寸 **`362×0`** —— 一句话定位；
> 4. 顺带实测出 B-210 / B-211 / B-212 三个「让故障查不下去」的问题。
>
> 教训：**「看不到原因」本身也是 Bug。** 降级链的每一条退出路径都必须留下可读的原因，
> 否则现场只能看到一句「所有协议均无法播放」，然后开始怀疑网关、摄像头、网线。

### 4.11 v1.1.0 修复的 Bug

| ID | 现象 | 根因 | 修复 | 文件 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| B-201 | `pnpm build` 卡住 **13 分钟以上不结束**，单个 node 进程内存涨到 850MB；但单独执行 `pnpm -F @eep/admin build` 只要 21 秒 | 原 `build` 脚本是 `pnpm -r --filter "./apps/*" build`，pnpm 递归任务默认并发执行 → **两个 `vue-tsc` 同时跑**，各占约 1.5GB，8GB 机器触发换页 | 改为显式串行：`pnpm build:screen && pnpm build:admin`；`typecheck` 加 `--workspace-concurrency=1`。并在 `package.json` 与文档中写明不可改回并行 | `frontend/package.json` | 已修复 |
| B-202 | `daily-check.sh` 执行到 5xx 占比统计时报语法错误 | bash 算术展开 `$(( ))` **不支持三目运算符** `?:`，误用了 JS 风格写法 | 先算分母再算百分比 | `deploy/linux/daily-check.sh` | 已修复 |
| B-203 | `web.config.admin` 出现两个 `<httpProtocol>` 节点，IIS 启动站点报配置错误 | 分别添加「安全响应头」与「缓存策略」时各写了一个同名节点，XML 中同名元素只允许出现一次 | 合并为单个 `httpProtocol`，并移除全局 `Cache-Control: no-cache`（会把 assets 的长期缓存也一起干掉） | `deploy/windows/web.config.admin` | 已修复 |
| B-204 | `openapi.yaml` 解析失败 | `TrendPoint.output` 的 `description` 少了收尾引号，写成 `description: 产量（瓶）' }` | 补回引号，并用 PyYAML 全量校验通过（21 接口 / 23 schema） | `backend/docs/openapi.yaml` | 已修复 |
| B-205 | `VideoView.vue` 模板里用 `window.open(...)` 无反应 | Vue 3 模板表达式只暴露白名单全局变量，**`window` 不在其中** | 改为在 `<script setup>` 中定义 `openSnapshot()` 方法 | `frontend/apps/admin/src/views/video/VideoView.vue` | 已修复 |
| B-206 | 目录重构后大屏能跑、后台构建慢十倍 | 前端工作区整体移入 `frontend/` 后，`pnpm build` 的并行问题（同 B-201）被放大 | 同 B-201 | 同上 | 已修复 |

### 4.12 v0.1.0 修复的 Bug

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

### 4.13 从旧版（v0.0.1）继承并已固化的口径类 Bug

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

### 4.14 已知问题 / 遗留风险

| ID | 问题 | 影响 | 处理计划 |
| :--- | :--- | :--- | :--- |
| K-001 | `energyModel` 强度参数为行业经验值，非实测 | 折算指标（单位电耗、水/汽耗强度）不能直接用于考核 | 试运行 1 个月后用真实历史数据回归拟合 |
| K-002 | 大屏 `ECharts` 整包引入，产物 gzip 后约 343 KB | 内网首屏加载约 1—2 秒，可接受 | 若要优化，改为按需引入 `echarts/core` + 具体图表类型 |
| K-003 | 后台 `element-plus` 全量引入，产物 gzip 后约 342 KB | 同上 | 后续可按需引入 + `unplugin-vue-components` |
| K-004 | `useViewport`（`composables/useEcharts.ts`）当前无调用点 | 死代码 | 下次迭代确认是否需要，不需要则删除 |
| K-005 | 后台下拉框选项为硬编码，未读数据字典 | 字典与页面可能不一致 | 迭代 P2-#18 统一改为字典驱动 |
| K-006 | 大屏非 16:9 屏幕四周留黑边 | 观感问题，非缺陷 | 属预期行为，见 `01-项目介绍` Q6 |
| K-007 | 视频首次播放依赖动态 import `hls.js` / `flv.js` | 首次切换产线有 300—600 ms 空白 | 可接受；若要消除，改为首屏预加载 |
| K-008 | 大屏默认链路延迟偏高（HLS，go2rtc 切片 0.5s，实测约 1—3s），比 MSE 的 0.5—2s 略高 | 看生产节拍时略有滞后感 | 属**已知取舍**（可靠性优先，见 ADR-008）。若现场确认大屏切到 mse 也能出画面，按 V 键切换即可，无需改代码 |
| K-009 | **网关未安装 ffmpeg** → MJPEG 拉流返回空响应、后台「抓图」返回 500 | 后台抓图功能不可用；大屏 mjpeg 兜底档位失效（HLS / MSE 不受影响） | 把 ffmpeg 放进网关 `PATH`（`deploy/go2rtc/README.md`）。装前可先不依赖抓图 |
| K-010 | `pnpm build` **偶发失败**（报 `error during build:` + `ERR_PNPM_RECURSIVE_RUN_FIRST_FAIL`，无更多信息），重跑即通过 | 偶发，不阻塞交付；但会误判为代码问题 | 重跑一次即可。已排除：代码错误（`typecheck` 通过）、产物锁（无预览服务在跑时同样出现）。疑似与内存/`pnpm` v12 安装期裁剪（`Packages: -61`）有关，建议**不要把 `typecheck` 与 `build` 串在同一条命令里**，8GB 机器上也不要同时跑浏览器等重进程 |
| K-012 | **车间进水总表接口未接入**（现场未提供地址 / cid / token） | 大屏「车间水耗」无数据；水耗强度无法计算 | 拿到后补配置并置 `enabled: true`，跑一次 `seed_registry`，**不需要改代码** |
| K-013 | **产量未采集**（灌装机计数器 / 装箱机 PLC / MES 班次报工均未对接） | 单位电耗、达成率、低产判定、产量热力图均无数据；v1.2.1 起接口如实返回 **null + available=false**（不再是 501），界面产量统一显示 `--` 并挂「产量未接入」标注 | 产量侧改造完成后：真实数据经 `ingest_production`（或采集脚本直接调 `write_production`）写入即可，接口与界面**自动恢复数值口径**（把 `dataConfig.productionEnabled` 置 `true`），无需改代码 |
| K-014 | 现场电量量级待核对（实测各表每小时增量在数十 kWh，若预期数百 kWh/h 则倍率有误） | 只影响数值大小，不影响差值逻辑 | 核对电表倍率 / CT 变比后改配置 `scale`，不用改代码 |
| K-011 | 若长期走 MJPEG 兜底，网关码率与 CPU 压力较大（逐帧转 JPEG，不压缩帧间差） | 墙挂大屏长期跑 MJPEG 会占用较多带宽与网关 CPU | 仅在 HLS 不可用时才会走到；建议摄像头用**子码流**（`Channels/102`），并优先把 HLS 修好 |

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

### 5.5 视频链路的三个反直觉之处（都是 B-207～B-212 的产物）

**① 布局塌成 0 高时，视频「有声无画」，而且看起来什么都不缺。**
`flex: 1 1 auto` 只在 **flex 容器**里才有意义。`.panel__body` 默认是块级元素
（只有显式传 `body-class="panel__body--col"` 才会变成 flex 列），
而 `.video` 的子元素全是绝对定位（内容高度 0）→ 容器塌成 **0 高**。
音频不依赖尺寸照样出声，画面却没有可绘制区域 —— 于是现场只看到「有声音、没画面」。
**结论：凡是 `flex: 1 1 auto` 的容器，都同时写上 `height: 100%` 兜底**（见 B-209）。

**② 「看不到原因」本身就是 Bug。**
失败必须留痕。这次排查最耗时的不是修，而是**没有线索**：
- 降级链递归时把 `tryLog` 清空了（B-210）；
- 组件挂载前的那次播放因为 ref 全是 null 而**静默 return**（B-211）。
两条叠加的结果就是：页面上永远只有一句「所有协议均无法播放」，一条原因都没有。
现在的规则：**每一条退出路径都要经 `fail()` 记录原因**，不许有静默分支。

**③ 「能加载」不等于「有画面」，探测动作本身可能破坏探测。**
判断一条视频链路是否可用，必须先分清哪些协议**能自证有画面**：

| 协议 | 能不能校验 | 校验依据 |
| :--- | :--- | :--- |
| hls（自有 `<video>`） | ✅ | `video.videoWidth > 0` —— 只有音频轨道时恒为 0 |
| mjpeg（`<img>`） | ✅ | `load` 事件在**收到真实帧**时才触发 |
| mse / webrtc（跨域 iframe） | ❌ | 只有 `load`，只能证明网页加载成功 |

规则不是「只准用可校验的协议」（那样等于白白放弃 MSE 的低延迟与免转码），
而是：**不可校验的协议后面必须跟一个能自证的协议兜底**（ADR-008）。
本次的真实教训是：**故障排查时，不可校验的「假成功」会把视线彻底挡住** ——
B-209 的容器 0 高就是这样被 `iframe.onload` 掩盖了整整一个版本的。

而且**别用 `display: none` 去藏正在探测的媒体元素**：
浏览器会跳过隐藏媒体的首帧解码，`loadeddata` 永远等不到，
探测逻辑等于把标的物自己藏起来了（B-212）。改用 `opacity`。

**顺带两个现场事实（实测）**

- `mode=mse` 是**白名单**语义。go2rtc 的 `video-stream.js` 只认 `mode` 里列出的协议，
  写 `mode=mse` 等于「MSE 不可用就彻底不播」，**不会自动往后降级**。
- **MJPEG / 抓图依赖网关侧的 `ffmpeg`。** 现场网关（go2rtc 1.9.14，`go2rtc_win64`）
  未安装 ffmpeg，于是 `/api/stream.mjpeg` 返回**空响应**、`/api/frame.jpeg` 返回 500
  并给出 `exec: "ffmpeg" executable file not found`。**HLS 与 MSE 不依赖 ffmpeg**，
  所以画面本身不受影响，但后台「视频监控」页的「抓图」按钮会失败。
  修复：把 ffmpeg 放进网关的 `PATH`（详见 `deploy/go2rtc/README.md`）。

**④ 跨域 iframe 的「脏东西」只能从外面裁，不能从里面改（B-214）。**
go2rtc 播放页在窄容器里会溢出并画出滚动条，而我们**无法给跨域 iframe 注入样式**。
可行的思路只有两条，都基于同一个事实：**滚动条永远画在 iframe 自身的右／下边缘**。

| 方案 | 做法 | 实测结果 | 代价 |
| :--- | :--- | :--- | :--- |
| ✅ 采用 | iframe 加 `scrolling="no"` | 内部可用宽 347 → **362**，滚动条消失 | 无（Chromium 仍支持该属性） |
| 备选 | iframe 放大 `calc(100% + 18px)`，让父级 `overflow:hidden` 裁掉 | 滚动条被裁到容器外 | 画面放大约 11%，右侧可能少 2—4px |

> 判断依据是「内部可用宽 vs 视野宽」：两者不等就说明滚动条占了位。
> 这条量法比肉眼看截图可靠，本地用两个静态服务 + 无头浏览器 5 分钟就能验证完。

---

### 5.6 后端采集侧（v1.2.0 换来的）

- **`pymodbus` 的 `retries=0` 会静默失败。** 传 0 时客户端**根本不发请求**，
  13 块电表全部返回「无响应」，现象与「485 断线 / 从站号写错」一模一样（B-301）。
  传 `retries>=1` 才正常。同理，串口服务器必须用 `FramerType.RTU` 组帧——
  它只是把 RS485 报文塞进 TCP，没有 Modbus TCP 的事务号/长度/单元号封装。
- **合计宁可空白，不给偏小值。** 「有几块表算几块」的和看起来正常，却会静默少算；
  缺数时的 `null` 反而能被发现（B-302）。
- **`progress` 要取「实际覆盖占比」，不能拿「已过时间占比」顶替。**
  刚上线时基线是本小时第一条读数，只覆盖几分钟，
  用已过时间占比会让人误读整小时的平均水平（B-303）。
- **接口层也别补 0。** 快照组装时写 `x or 0` 很顺手，但那正是铁律禁止的行为（B-304）。

### 5.7 数据闭环侧（v1.2.1 换来的）

- **`watch(immediate)` 喂数据 + 异步初始化的组件 = 首帧静默丢失。** `immediate` 回调在 setup 阶段执行，
  那时模板 ref 还是 `null`；如果封装层没有「挂载前记账、挂载后补渲染」的机制，第一次喂进去的数据
  就无声无息地丢了，等下一次数据变化才恢复 —— 表现就是「第一次进页面图表空白，切走再切回来才有」
  （B-401）。**任何 `onMounted` 前可能收到数据的封装，都必须有 pending 缓冲**。
- **「前后端接口路径」必须写进契约并两端对表。** 后端把 admin 路由挂在 energy 前缀下、前端按契约调
  `/api/admin/*`，两边各自都「看起来对」，合起来就是全量 404（B-404）。这类问题在 mock 模式下
  完全暴露不出来 —— 切 api 模式前应先跑一遍 `curl` 冒烟。
- **Django `JsonResponse` 默认 `safe=True` 只收 dict。** 返回数组的接口忘了 `safe=False` 就是 500，
  而且报错文案相当绕（B-405）。统一出口函数里直接固化 `safe=False` 最省心。
- **「还没接数据」和「确实没产出」要用不同的值表达。** 产量未接入期间用 0 占位，现场会读成
  「全线停产」；用 null + 界面 `--` + 显式标注，三个信号共同说明「这不是生产结论」（B-406）。

### 5.8 采集口径与依赖版本（v1.2.2 换来的）

- **日期时间相关的「今天」，永远不要用 `toISOString().slice(0,10)`。**
  它按 UTC 算，东八区在早上 8 点前会得到**昨天**。前端所有日期字符串统一用本地时间拼。
- **第三方库改了参数名，报错却像协议不兼容。** pymodbus 把 `slave=` 改成 `device_id=`，
  报错是 `unexpected keyword argument`，但现场看到的是「13 块表全部读不到」，
  第一反应必然去查接线。**锁版本 + 按签名探测**两道保险一起上，才不会白跑现场。
- **「没采到数」和「采到了 0」必须能区分。** 旧实现两者都会产出 0，
  而 0 在图上、在表里都表示「确实没用」—— 一旦混了，整块数据的可信度就没了。
  凡是聚合，都要问：**缺数据的分支返回的是什么？**
- **补采/重试会改变边界语义。** 一旦允许同一采样槽内多次采样，
  端点就**不能**取「区间内最后一条」，否则区间被拉长（B-503）。
  先想清楚「哪一条代表这个边界的值」，再写聚合。
- **`reactive()` 与普通对象在模板里的行为不同。** 组合式函数返回多个 ref 时，
  要么返回 `reactive`，要么让调用方解构；直接返回普通对象会让模板拿到恒为真的 ref
  （B-506 的横幅永远显示就是这么来的）。

### 5.9 趋势图与可空字段（v1.2.3 换来的）

- **类型声明撒谎，编译器就帮不了你。** 接口早就在返回 `kwh: null`，而 `TrendPoint.kwh` 写的是
  `number` —— 于是 `x.kwh.toFixed()` 一路通过 `vue-tsc`，到运行时才抛 `TypeError`，
  症状还不是报错而是「这块图安静地空了」（B-513）。
  **接口可空的字段，类型里必须写可空**；改完之后编译器会把每个取值点都点一遍名，
  这一步是省不掉的（B-509 是同一条规律在后端的翻版：字段改可空、序列化层漏改）。
- **`null` 参加算术会被当成 0，参加方法调用会抛异常。** 同一个空值，
  `(x.kwh / 1e4).toFixed(3)` 得到 0（画出「暴跌到零」的假曲线），
  `x.kwh.toFixed(0)` 直接炸（整张图消失）。两种都不许留，出数口统一走一个空值安全的 `scale()`。
- **时间序列的顺序必须在接口层定死。** 一旦两端各按各的假设写（接口出「新→旧」、
  前端按「末项 = 本期」取去年值），拼起来就是「较上年」比成三年前（B-512）。
  规则：**数组顺序 = X 轴顺序 = 时间正序**，前端不做 `reverse()`。
- **显示单位与除数是同一件事的两半，必须放在一起。** 除数写在图表工厂、单位文案写在模板里，
  改一处忘一处，结果图例说「万瓶」、tooltip 说「亿瓶」（B-514）。
  合并成一张 `Record<TrendDim, { 除数, 单位 }>` 之后，模板和图表读同一份，不可能再漂移。
- **固定单位会把小值舍成 0。** 单线一年 23 kWh，在「万kWh」轴上按两位小数就是 `0`，
  读起来是「这一年没用电」。与 B-101（电量小于 20 kWh 保留 1 位小数）同源：
  **非零值被舍成 0 时自动加精度**，而不是让读者去猜那个 0 是不是真的 0。
- **图例只写「年/月/日/时」一个字，不足以说明这张图。** 必须写清「每柱＝1 天」还是「每柱＝1 小时」，
  否则同一张图上两个维度看起来只差柱子疏密（v1.2.3 现场反馈）。

### 5.10 图表观感与自动化验证（v1.2.4 换来的）

- **「没对齐边界」通常不是对齐问题。** 六线对比面板被反馈成「没对齐」，实测画布上有墨的像素
  只横跨 `[4, 40] / 958` —— 是**整块没画**。先量像素跨度，再决定改 margin 还是改数据口径。
- **ECharts 对 `null` 值：柱不画，挂在柱端的 `label` 也不画。** 所以「值缺失时靠标签兜个 `--`」
  这个假设是错的。要让缺数仍然可读，得给它**不依赖那个缺失数据源的画法**
  （本项目的做法：产量未接入 → 改画用电量 + 占比）。
- **右侧留白要按最长标签算，不是随手一个数。** `grid.right: 132` 装不下
  「52.75 万瓶 · 7.71 kWh/千瓶」（约 155px），产量一接入就会右边缘裁字（B-515）。
- ⚠ **用无头/后台浏览器验证 ECharts 时，必须先关掉动画。** 页面不在前台时 `requestAnimationFrame`
  不推进，折线的「生长动画」停在第一帧 —— 现象是**只剩圆点、没有线也没有面积填充**，
  极易被误判成「areaStyle 没生效」。正确做法：
  `inst.setOption({ animation: false }); inst.resize();` 之后再读画布像素或导出图片；
  或者把 option 拿到一个 `animation: false` 的离屏实例里渲染再看。
  （本轮就先踩了这个坑，据此得出的「总览也没有阴影」结论是错的。）
- **`getOption()` 的输出不能整份搬走重画**：`label.formatter` 等函数字段会被 `JSON.stringify` 丢掉，
  离屏重画时标签会退化成只显示数值。要验标签文案，就在**活实例上关动画**，别用序列化副本。

### 5.11 布局与状态语义（v1.3.1 换来的）

- **`PanelBox` 的 `height` 与 `grow` 是互斥的，一列里只能有一个「撑满者」。**
  不传 height 才会加 `panel--grow`（`flex: 1 1 auto`）。同一列里如果每块都写死高度，
  这一列就填不满板面，**底边必然和相邻列不齐**（B-517）。
  约定：**每列最后一块面板不传 height**，由它吸收剩余空间；前面的才用 `--l1 / --c1 / --r1` 定量。
  改完要量一遍三列末块的 `getBoundingClientRect().bottom` 是否相等 —— 24px 的差目测看不出来。
- **一个布尔不要扛两种意思。** `partial` 同时表示「这个小时还没结束」和「这个小时的合计缺数据」，
  前端拿它渲染状态，就把历史异常小时写成了「进行中」（B-518）。
  这类错不报错、不白屏，只给出一个**看起来合理的错状态**，比崩溃更难被发现。
- **接口字段只要出现在响应里，就要进类型。** 后端一直在发 `quality`，
  类型没声明就等于把它扔掉，前端只能靠猜。这与 B-513（类型写 `kwh` 非空、接口给 null）
  是同一处失守的两种表现：**契约的对齐必须落在 `types.ts` 上，而不是落在口头约定上。**
- **演示数据（mock）必须走同一套字段。** 给 `HourPoint` 加 `quality` 时若只改后端，
  mock 模式下该字段缺失、状态列走兜底分支，两种数据源显示不一致，
  排查时会被误判成「接口坏了」。`mock/energy.ts` 已同步补齐。

### 5.12 「大屏和后台对不上」的四种不同病因（v1.4.1 换来的）

现场报「数字对不上」时**不要先怀疑算术**，这次四个原因里没有一个在算错加减：

- **`day == now.date()` 在「查询别的日期」时恒真。** 为了表达「历史日整天都结束了」，
  接口把 as-of 造成那天 23:59:59 —— 于是快照层认为「今天就是这天」，最后一个小时被当进行中，
  实时窗口又没有上界，一格装下十个小时的用量（B-522）。
  **周期是否结束是调用方的意图，必须作为参数传进来，不能从时间戳反推。**
- **只有下界的时间区间是定时炸弹。** 真实时钟下未来还没有读数，所以看着永远没事；
  一旦 `now` 是造的，它就一路读到当前最新读数。展示型查询一律写**闭/开上下界都全**。
- **「最后一个小时」要等次日才算得出来，所以汇总窗口必须包含昨天。**
  日 / 月 / 年三个尺度的偏小不会报错，只会让大屏与后台永远差一格（B-523）。
  凡「滚动窗口」的汇总，写代码时先问：这条数据的**终点**什么时候才齐？
- **两边精度口径不一样，也是「对不上」。** ≥20 kWh 取整看起来整洁，代价是几乎每行差 0.x，
  真正的问题被这种假差异淹没（B-524）。同一份数据跨界面展示时，**精度口径属于接口，不属于版面**。

另外两条流程性教训：

- **异步快照必须带请求序号。** 接口 2—4 秒、用户连点两次日期，后发先至的旧响应会把新画面盖掉 ——
  现象是「B 日的数字挂在 A 日的标签下」，比白屏更难被怀疑（B-525，反馈的 7.3 就是另一天的同格值）。
- **后端把字段改成可空，第一个改动必须是 `types.ts`。** 否则 `x.toFixed()` 照样通过编译，
  运行时抛错带走整棵组件子树（B-526 与 B-521 同因，两次都靠控制台才看出来）。

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
| ADR-008 | 大屏默认链路 **`mse → hls → mjpeg`**：mse 最优先，但**后面必须跟一个能自证有画面的协议**；不可校验的协议（mse / webrtc）不放在链尾 | mse 延迟最低、网关不转码，现场实测画面正常（B-209 修复后），没有理由为了「可校验」而牺牲它；但它由跨域 iframe 承载、前端无法确认有没有画面，所以必须有 hls 兜底，避免「假装成功」卡死降级链（B-207） | 把不可校验的协议排在链尾（看似稳妥，实则浪费 mse 的低延迟与免转码优势）；或让前端自己实现 MSE（fMP4 的 codec 串随摄像头档位变化，写死会在部分型号黑屏） |
| ADR-009 | 大屏默认**不出声**（`<video muted>`；HLS 输出本身也不带音轨） | 车间墙挂大屏放摄像头声音没有意义，还会掩盖排障信息（这次「只有声音」反倒成了唯一线索） | 保留音频（现场噪音 + 干扰判断） |
| ADR-011 | **现场设备登记表放在配置文件里**（`config/settings.yaml` 的 `gateways` / `meters`），用 `seed_registry` 幂等同步进库 | 现场设备表本来就是 Excel 形式（一行一台），配置文件的形态最接近；走 Git 能留痕能评审；新增一块表就是一次可 review 的提交，且**不用改代码** | 让人在后台页面上一条条录（新装现场要录 15 条，容易漏、且没有版本记录） |
| ADR-012 | **小时差值取「上小时末读数」当起点**（`usage(H) = end(H) − end(H−1)`） | 相邻小时首尾相接，不漏计也不重复计；采集中断后恢复，也不会把中断期间的用量错算进某一个小时。端点离整点太远会被判 `partial` 而不是造假 | 用「本小时第一条读数」当起点（每小时都会丢掉整点附近那段，且中断后会把跨小时用量堆到同一格） |
| ADR-010 | 视频面板高度用 `body-class="panel__body--col"` + `.video{height:100%}` 双保险 | `flex: 1 1 auto` 在块级父元素里完全失效，容器会**静默**塌成 0 高；而「有声音没画面」这种症状极难与网络/编码问题区分（B-209） | 只依赖 flex（已踩坑）；或写死像素高度（换分辨率就错） |
| ADR-013 | **产量未接入期间，一切产量占位统一为 null（界面显示 `--`）**：库内占位行 `output=NULL` + `source=demo`/`quality=nodata`，接口层据此一律返回 null 并挂「产量未接入」标注（B-406） | 0 会被现场读成「确实没产出 / 全线停产」，null 才是「还没接数据」；占位行带 source/quality 标记，接口层能可靠区分「演示占位」与「真实产量」，真实数据源接入后**自动恢复数值口径**（置 `productionEnabled=true` 即可），不用改渲染代码 | 占位写 0（语义混淆，v1.2.0 后期短暂采用过）；接口返回 501（前端要为「未接入」写特殊分支，v1.2.0 方案） |
| ADR-015 | **采集节奏默认「每小时一次」**（整点 + `collect.hourly_offset_seconds`），差值区间因此严格等于 1 小时 | ① 两个端点偏移相同 → 区间正好 1 小时，不累积偏差（密采样时端点间隔会随轮次耗时漂移，小时值稳定偏一点，月/年累计看得出来）；② 与现场抄表/结算的节奏一致，数据量降为 1/60；③ 采样时刻由「整点」定义，重试与补采不会改变边界语义 | 密采样（每 60 秒）：随时能给本小时参考值、单点失败有冗余，但有相位偏差（已实测确认）；两种节奏的**差值口径完全一致，切换只改一行配置** |
| ADR-016 | 差值端点取「**距整点最近且在 ±20 分钟容差内**」的读数；超出容差判缺数 | 最近 → 采样槽内第一条永远胜出，补采不会拉长区间（B-503）；容差 → 杜绝「拿隔天读数相减得到 0」这类假数字（B-502）；±20 分钟同时覆盖密采样（整点前几十秒）与整点采样（槽内几分钟）两种节奏 | 只设上界（会产生 0 —— B-502）；要求严格落在采样槽内（密采样的历史数据全部无法配对） |
| ADR-017 | 历史日期一律按「**整天已结束**」取值（23:59:59） | 看历史就是为了看完整的一天；沿用当前时分会让下午到深夜的小时被判成「还没到」而留空（B-507） | 沿用当天当前时分（B-507） |
| ADR-014 | **大屏与后台同读一张表**：日月年总计一律读 `eep_period_summary`；产线名称 / 额定产能 / 品项一律以数据库为准（大屏不再读前端静态 `linesConfig`）；仪表配置支持**配置文件 seed 与后台维护双通道**，写操作置 `source=admin`，`seed_registry` 默认不覆盖后台改过的记录 | 「两边数字必须一致」只能靠同源保证，任何「各自现算」的捷径都会分叉（B-404 的教训：后台一度根本没连上真实库） | 前端静态配置当真值（大屏与后台必然漂移）；后台可随意覆盖配置文件登记（seed 幂等性被破坏） |
| ADR-019 | **周期取数按「是否还在进行」分流**：包含今天的周期（今日 / 本月 / 本年）一律从 `energy_hourly` 现算，已结束的周期才读 `energy_daily`·`monthly`·`yearly`；汇总表的结算降为**每天 00:30 一次**（`summarize --rollup`），采集任务不再每小时重刷。规则实现在 `summary.is_open_period()`，由 `period_rows()` 统一出口，两个界面共用 | 现场要求「月日数据每天更新即可」，但大屏的当期数字不能整天不动 —— 降频与实时只能靠这条分流同时满足。未来的小时在小时表里本来就没有行，现算天然等于「截至此刻」，不需要再做时间截断；同源同口径的约束（ADR-014）也守住了，因为分流发生在同一个函数内 | ① 继续每小时刷汇总：当期数字依赖汇总表行，白耗一次全表扫描；② 只降频不分流：大屏与后台的「今日 / 本月」整天冻结，值班以为系统卡死；③ 前端自己判断「哪根该现算」：又回到两端各表一套假设（ADR-014 / B-512 的老路） |
| ADR-018 | **趋势序列的时间顺序在接口层定死为「正序」**：`daily30` / `monthly12` / `yearly5` 一律最旧在前、末项 = 本期，X 轴直接按数组顺序铺开，前端不做 `reverse()`；显示单位与除数合并成一张表（`OVERVIEW_TREND` / `LINE_TREND`），图表与图例同读一份 | 顺序与单位一旦允许两端各表一套，就会同时出现「X 轴反着读」和「图例说万瓶、tooltip 说亿瓶」（B-512 / B-514）。同源是唯一可靠的做法，与 ADR-014 同理 | 前端拿到倒序再 `reverse()`（等于两处都对顺序有假设，改一边就错）；单位文案写死在模板里（B-514 的现状） |

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
- [ ] **首次进入**总览与单线页，中列 KPI 卡与全部图表**一次渲染成功**（不空白、无需切产线刷新，B-401/B-402）
- [ ] 产量未接入期间：所有产量位显示 `--`（不是 0），页面上有「产量未接入」标注；用电数据正常有值（B-406）
- [ ] 快捷键 `1` / `2` / `F` / `R` 生效
- [ ] 单线页视频面板出画面，右上角角标显示当前协议（`MSE` / `HLS` / `MJPEG`），**且面板右侧没有滚动条**（B-214）
- [ ] 单线页按 `V` 可循环切换视频链路，右下角弹出提示，刷新后保持上次选择
- [ ] 关掉 go2rtc 网关：视频面板应在超时后显示占位与「失败记录」，**不是白屏、也不是黑屏留声**
- [ ] 点击总览的产线行能跳到对应单线详情
- [ ] 单线页产线 tab 名称、额定产能、品项与后台「生产管理」完全一致（同读数据库，B-404 / ADR-014）
- [ ] 趋势维度切换（年/月/日/时）图表与说明文字同步变化（**总览与单线页都查**）
- [ ] 趋势图 X 轴**从左向右时间递增**：最右一根是当前周期（年→今年、月→当月、日→今天、时→23 时）（B-512）
- [ ] 趋势图**四个维度都要单独点开看一次有没有画出来**（画布不能全空）；早上线、缺数周期多时最易复现（B-513）
- [ ] 缺数周期在图上**留空**（柱不画、线断开），不是零高度柱、也不是「暴跌到 0」（B-513）
- [ ] 图例单位与 tooltip 单位一致：年＝亿、月/日＝万、时＝千（B-514）
- [ ] 趋势图说明写明「每柱＝1 年 / 1 个月 / 1 天 / 1 小时」，四个维度切换时该文字同步变化
- [ ] 总览下方「六条产线对比」面板**产量未接入时仍有内容**：条长＝今日用电量、右侧显示「kWh · 占车间%」，不是空面板（B-515）
- [ ] 六线对比的右侧标签**完整可见不被裁**（把画布右边缘的像素跨度量一遍，别只看截图）
- [ ] 单线趋势图曲线**每个时间点有描边圆点**，「用电量」形态下折线下方有渐变阴影，观感与总览一致（B-516）
- [ ] 以上两条在自动化浏览器里验证时**必须先 `setOption({animation:false})` + `resize()`**，否则折线动画不推进，会误判成「没有线 / 没有阴影」
- [ ] 总览与单线页：**三列最后一块面板的底边必须齐平**（量 `getBoundingClientRect().bottom`，别目测）（B-517）
- [ ] 「本线小时明细」状态列：只有**当前整点那一行**是「进行中」；已结束的异常整点必须显示 `异常·缺数 / 异常·换表 / 异常·尖峰 / 异常·部分缺数`，**不允许历史小时出现「进行中」**（B-518）
- [ ] 「今日分时」图 tooltip 的状态后缀与明细表同一套判定（异常小时不能写进行中）
- [ ] `dataConfig.mode` 切 `mock` 与 `api` 两种模式，状态列文案一致（mock 也带 `quality`）；验证完记得改回 `api`
- [ ] **切到任意历史日期后，23:00 那一格必须与后台同格数值完全相等**，且状态不是「进行中」
      （B-522 的原症状：09-27 一线 23 点大屏 85 / 后台 8.1；接口层一行 curl 就能对出来）
- [ ] **大屏与后台逐格比对**：对最近 5 天 × 六线跑一遍 `overview?date=` 与
      `range?granularity=hour&scope=all`，把每格差值 > 1e-9 的行列出来，必须为 0 处
      （这一条是唯一能同时兜住 B-522/B-523/B-524 的检查，别只看一眼截图）
- [ ] 大屏任意小时的电量**小数位与后台表格一致**（都是 1 位，整数不带 `.0`）（B-524）
- [ ] 快速连切两个日期：最终画面必须属于**最后选的那一天**；切换过程中日期控件徽标显示
      「正在取数 / 切换中」，不允许出现「新日期标签 + 旧日期数字」的静默错位（B-525）
- [ ] 历史日下所有「本小时 / 今日累计」类文案改为「23 时段 / 当日累计」
- [ ] 查历史日时水/汽的**瞬时流量整块不显示**（而不是显示 0 t/h —— 0 是「此刻没流量」）（B-526）
- [ ] **跨零点挂机测试**：把系统时钟（或测试用的假 `Date`）推过 00:00 后**不刷新浏览器**，
      大屏日期控件应自动翻到新的一天、徽标保持「实时」，并自发出一次
      `overview?date=<新一天>` 请求；回看历史日期时则**不该**被零点拽回今天（B-527）
- [ ] 零点后 00:00—00:30 之间，「近 30 日」的**昨天那一根不能是空的**（未结算完整时退回现算，B-528）
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
- [ ] 网关列表 / 计量点位 / 生产管理 / 数据质量 / 采集日志均显示**真实库数据**（5 台网关、15 个计量点、6 条产线，B-404/B-405 修复后）
- [ ] 网关「连接测试」与计量点「试采」返回真实读数与原始报文（现场调参主力工具）
- [ ] 「用量查询」日/月粒度：明细表缺数显示 `--`、趋势图缺数处留空不补 0，时间列从左向右递增（B-512 / B-513）
- [ ] v1.4.0：电 / 水 / 汽三个计量页的明细表**都必须有行**（曾经三页恒空，只有汽页被发现，B-519）
- [ ] v1.4.0：计量页与用量查询的明细表**右侧不得留大片空白**（列一律 min-width，B-520）
- [ ] v1.4.0：数据中心起止时间精确到小时，改区间后 KPI、主图、三张累计图**同时变**；`今日` 字样已换成 `区间累计`
- [ ] v1.4.0：用量查询勾选「水 / 汽」时车间合计要出曲线；切到「单条产线」时水汽勾选**必须禁用并写明原因**（不能画一条全零线）
- [ ] v1.4.0：产量未接入时「累计产量 / 累计单耗 / 单箱变动费用」全部 `--`，且页面上能看到 `cost.blockers` 给出的原因
- [ ] v1.4.0：标准变动费用未标定时**不画基准虚线**；在「系统参数」页标定后刷新即出现
- [ ] v1.4.0：系统参数页改动要真实落库（刷新后仍在），清空 ≠ 填 0
- [ ] v1.4.0：「仪表读数查询」按网关 / 仪表 / 时间三级筛选有结果，采集失败行读数显示 `--`，导出 CSV 中文不乱码
- [ ] ⚠ 后台页面自动化验证时留意：**mounted 钩子里抛异常会中断整棵子树挂载**，症状常出现在别的组件上（B-521 就是这样把表格拖空的）—— 排查务必先看 console

### 7.4 接口切换

- [ ] `dataConfig.mode` 为 `'api'`（v1.2.1 默认）：页面无报错、数值与能源系统对得上；停掉后端再打开，大屏应显示「演示数据」降级提示而不是白屏或假装真实数据
- [ ] 断开某条线的电表，页面应在「数据质量」显示缺失率，并出现「电表无数据」告警
- [ ] 视频网关配好后，A1 详情页左上角出图，且**没有声音**；
      **关掉网关应降级为占位（含失败记录）而不是白屏或黑屏留声**
- [ ] 后台「视频监控」页点「检测网关」返回「网关可达，共 N 路流」，且各通道「配置校验」与网关实际流名一致

### 7.5 采集与接口（后端）

- [ ] `python manage.py collect` 全部点位成功（现场标准：14/14，约 1.4 秒）
- [ ] `python manage.py test apps.energy` 44 个单元测试通过
- [ ] 断掉一块表的网线：该点位失败、**其余点位照常入库**，`eep_collect_log` 有失败原因
- [ ] 缺数小时在 `eep_hourly_usage` 里是 `null` + `quality=missing`，**不是 0**
- [ ] 拔掉某块表再插上（模拟换表）：该小时判 `reset`，不产生负数
- [ ] `curl /api/energy/overview` 返回结构与 `types.ts` 的 `EnergySnapshot` 一致
- [ ] `curl /api/energy/production` 返回 `available=false` 且产量值为 **null**（演示占位不是 0，是预期行为）
- [ ] `curl /api/admin/gateways` 与 `/api/admin/quality` 返回 200（数组接口，不再 500）
- [ ] 后台改一个计量点的从站号后点「试采」，返回**真实读数**；`seed_registry` 重跑后该修改不被覆盖（source=admin）
- [ ] 计划任务连续跑过 24 小时：每小时 60 轮、无任务堆积
- [ ] **两个任务都在**：`EEP-Collect`（每 5 分钟，只出小时数）与 `EEP-Rollup`（每天 00:30，结算日/月/年）。
      只有前者时当期数字照样在动，**「近 30 日」却会无声停在几天前**（v1.4.2）
- [ ] 大屏与后台的日/月/年 KPI 同源同值；**当天那一根/本月/本年必须随整点增长**，
      即使汇总表是旧的（当期一律从小时表现算，见 `summary.is_open_period`）

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
