# 接口契约（API Contract）

> 版本：v1.5.0 ｜ 最后更新：2026-10-09
>
> **本文件是前后端的唯一约定。** 前端已按本契约预留好取数层
> （`frontend/packages/shared/src/api/index.ts`），
> 只要后端返回结构与此一致，前端渲染代码**一行都不用改**。
>
> ⚠ 修改契约时必须**同时更新三处**，否则会出现隐性错配：
> ① 本文件 ② `frontend/packages/shared/src/types.ts` ③ `docs/openapi.yaml`

---

## 〇、v1.2.2 实现状态（先看这个）

> v1.2.2 新增：`GET /api/energy/range`（时间段查询，见 2.4）；
> `/api/energy/hourly` 支持 `from`/`to` 区间与分页；
> 采集节奏改为**每小时一次**（`collect.cadence = hourly`），差值区间严格 1 小时。

## 〇·补、v1.2.1 实现状态

| 接口 | 状态 | 说明 |
| :--- | :--- | :--- |
| `GET /healthz` | ✅ 已实现 | 健康探测 |
| `GET /api/energy/overview` | ✅ 已实现 | 大屏总览（`EnergySnapshot` 同构） |
| `GET /api/energy/line/{code}` | ✅ 已实现 | 单线详情（`LineData` 同构，含 daily30 / monthly12 / yearly5 供年/月/日/时切换） |
| `GET /api/energy/hourly` | ✅ 已实现 | 小时用量明细（支持 `date` / `line` / `meter` / `kind`） |
| `GET /api/energy/meters` | ✅ 已实现 | 计量点台账 + 最近读数 + 今日用量 |
| `GET /api/energy/collect-logs` | ✅ 已实现 | 采集日志 |
| `GET /api/energy/collect-summary` | ✅ 已实现 | 24 小时采集汇总（巡检卡片用，非前端契约字段） |
| `GET /api/energy/period-summary` | ✅ 已实现 | 日 / 月 / 年总计（读 `eep_period_summary`，后台同源） |
| `GET /api/energy/production` | ✅ 已实现 | 产量采集尚未改造：**如实返回 null + available=false**（不是 501，不是 0） |
| `GET/POST/PUT/DELETE /api/admin/gateways*` `meters*` | ✅ v1.2.1 已实现 | 网关 / 计量点后台增删改；`POST …/{id}/test` 连接测试与试采（返回**真实读数**） |
| `GET /api/admin/overview` `lines` `quality` `collect-logs` | ✅ v1.2.1 已实现 | 后台看板 / 产线主数据 / 数据质量 / 采集日志 |
| `/api/admin/*` 其余（系统管理、告警、操作/登录日志、报表） | ⬜ 未实现 | 前端这些域暂走演示数据（`dataConfig.mockOnly`） |

> ⚠ v1.2.1 修复：admin 组接口曾误挂在 `/api/energy/admin/*` 下导致前端 404（B-404），
> 现已按本契约挂到 **`/api/admin/*`**；旧地址 `/api/energy/admin/*` 兼容保留但不再推荐。

**产量口径（v1.2.1 起为 null，前端切 `mode: 'api'` 前必读）**

1. **产量相关字段一律为 null / 0**：产量数值（`output`、`today.output`、`monthly12[].output` 等）
   在数据源未接入期间一律为 **null**（界面显示 `--`），`unitKwh` 为 null，`plan` / `rate` 为 0。
   后端不采集产量，也不拿电量反推产量。null 表示「还没接数据」，0 会表示「确实没产出」—— 两者不能混。
   真实产量经 `ingest_production` 写入后，接口自动恢复数值口径（`source != demo` 即视为真实）。
2. **水 / 汽的 `today`、`yesterday` 可能为 `null`**（未知 ≠ 0）。
   `types.ts` 中 `UtilityMetric.today / yesterday` 与 `LineData.today.kwh` 等字段类型
   已放宽为 `number | null`，前端按 `--` 显示，不把「还没采到」显示成「用了 0」。
   同理 `HourPoint.kwh` 已经是 `number | null`。

---

## 〇·补二、v1.2.3 趋势序列约定（顺序 + 空值）★ 前端渲染必读

适用字段：`daily30` / `monthly12` / `yearly5`（`/api/energy/overview`、`/api/energy/line/{code}`、
`/api/admin/overview` 三处同构），元素类型为 `TrendPoint`。

1. **顺序一律时间正序**：数组第 0 项最旧、**末项是当前周期**（今天 / 当月 / 今年）。
   X 轴直接按数组顺序铺开即可，**前端不要再 `reverse()`** —— 顺序由接口层定死（ADR-018）。
   需要「上一期」做同比时按下标取（如 `yearly5[length-2]` = 去年）。
2. **`kwh` 可空**：`TrendPoint.kwh: number | null`。该周期**没有任何可信小时差值**时为 `null`
   （不是 0），前端渲染成留空 / `--`。
   ⚠ 类型曾误声明为 `number`，导致 `x.kwh.toFixed()` 通过编译却在运行时抛异常，
   **整张趋势图不渲染**；把 `null` 兜成 0 又会画出「用量暴跌到零」的假曲线（B-513）。
   折线必须 `connectNulls: false`，不许拿两端凑中间没采集的那段。
3. **`output` 可空**：产量未接入期间恒为 `null`（见上文「产量口径」），接入后自动恢复数值口径。
4. **显示单位由前端按维度决定，且必须与图例同源**：
   后端只给原始 `kWh` / `瓶`，缩放与单位文案在
   `apps/screen/src/charts/index.ts` 的 `OVERVIEW_TREND` / `LINE_TREND` 里成对定义，
   图表与图例都读这一份（B-514 的教训：除数写在图表、单位写在模板，两边会漂）。

---

## 〇·补六、v1.5.1 字段命名约定 ★ 改字段名必读

**接口层对「认不出来的字段」是静默丢弃的**（`_apply()` 拿字段名在模型白名单里找不到就跳过），
不报错、不告警、照样返回 200/201。现场只能看到「那一格是空的」，或者更糟 ——
字段被**默认值顶掉**（B-535：网关 `ip` 丢失；同源的 `factor` 丢失使倍率恒为 1）。

因此下面这两条别名表是**必须与前端契约逐字对齐**的，改字段名要三处一起改
（前端提交名 / 别名表 / 模型字段名）：

| 前端提交（`types.ts`） | 模型字段 | 说明 |
| :--- | :--- | :--- |
| 网关 `ip` | `host` | ⚠ 名字不同，**必须走别名**。漏了它 IP 写不进去 |
| 网关 `baseUrl` / `timeoutMs` / `intervalSeconds` | `base_url` / `timeout_ms` / `interval_seconds` | camelCase → snake_case |
| 计量点 `factor` | `scale` | ⚠ 名字不同，**必须走别名**。漏了它倍率被默认值 1 顶掉 |
| 计量点 `funcCode` / `wordOrder` / `urlPath` / `valuePath` / `timePath` / `maxHourly` / `assetNo` / `installDate` | 对应 snake_case | 同上 |
| 计量点 `kind` / `scope` / `role` / `lineCode` / `gatewayId` | 由 `_save_meter` 直接读 `data` | 不走 `_apply`，但**改名字要同时改 `_save_meter`** |

**机器可查的兜底**：`api.assert_alias_covers_frontend()` 会把前端契约里所有字段
逐个对一遍别名表，`AppConfig.ready()` 在应用启动时调用 —— 有漂移就直接写进启动日志。
新增前端字段后，`FRONTEND_GATEWAY_KEYS` / `FRONTEND_METER_KEYS` 也要同步补上。

**可空字段的空值**：表单没填时前端给的是空串 `""`，跨层必须转成 `NULL` 再入库
（`DateField` 遇到 `""` 会抛 `ValidationError`；数值字段会被当成非法值）。
接口层对这类校验失败一律返回 **400 + 逐字段原因**，不漏成 500（B-536）。

---

## 〇·补五、v1.5.0 新增接口：读取当前值（**只读，不落库**）

### `POST /api/admin/meters/{id}/current` ★ 读取某个计量点的当前值

请求体（可空；两个字段都可省，省略即用后端配置）：

```jsonc
{
  "timeoutMs": 800,     // 单次读写超时，后端裁剪到 200—10000ms
  "retryTimes": 2       // 失败后的额外重试次数，裁剪到 0—5（总尝试 = retryTimes + 1）
}
```

响应（`MeterCurrentResult`，前端类型同名）：

```jsonc
{
  "meterId": 4, "meter": "E-A4-01", "name": "四线 生产线电表",
  "category": "电", "lineCode": "A4",
  "gatewayCode": "GW-TASHI", "gatewayName": "一期配电房塔石",
  "protocol": "Modbus RTU over TCP",
  "address": "10.112.66.133:8890 / 从站 11 / 寄存器 1000×2",
  "unit": "kWh", "factor": 0.1, "enabled": true,

  "ok": true,
  "value": 639024.4,                 // 读不到是 null，**不是 0**
  "raw": "slave=11 addr=1000 regs=0061 81e4",
  "error": "",
  "extra": { "raw_int": 6390244, "slave": 11, "register": 1000 },
  "attempts": 1,                     // 本次实际尝试次数（>1 说明靠重试拿到了值）
  "at": "2026-10-09 15:58:40",
  "durationMs": 541,

  "timeoutMs": 800, "retryTimes": 2, // 本次**生效**的参数（可能来自请求覆盖或后端默认）
  "persisted": false,                // 恒为 false
  "note": "本次读取只发往现场设备一次，不写数据库、不写任何日志，也不更新点位的最近读数"
}
```

**调用方必须知道的四条：**

1. **不落库。** 不写 `energy_raw`、不更新 `energy_meter` 的
   `last_value / last_at / last_ok / last_error`、不写采集日志。
   `last_*` 是链路健康信号（列表页的「在线 / 离线 / 故障」就靠它），
   「看一眼当前值」不能把它刷成刚刚成功，否则断讯的表会被人工「治好」。
2. **与 `POST /api/admin/meters/{id}/test` 的分工**：那是「试采」，会更新 `last_*`，
   用于调参后确认配置；本接口用于**只看一眼、不留痕**。需要留痕就用试采，别改这里的语义。
3. **失败**（网络不通 / 从站号写错 / 寄存器地址写错）一律返回 **HTTP 200 + `ok:false`**，
   失败原因在 `error` 里 —— 这是排障接口，不该把 500 抛给界面。
   点位不存在才是 404。
4. **停用的点位也允许读取**：响应里 `enabled:false` 提示调用方。
   手动读取不受启用状态限制（排障本来就要读停用的表），但后台采集任务不会采它。

超时预算 = `timeoutMs × (1 + retryTimes)`，最坏 10000 × 6 = 60s，与采集侧口径一致。

## 〇·补四、v1.4.4 行为变更（两处，都影响「看到什么」）

### 1) `GET /api/energy/range` 的 hour 粒度**只到最后一个已结算整点**

小时用量的定义是 `usage(H) = read(H+1:00) − read(H:00)` —— **当前这个小时要到下个整点
采到端点读数才算得出来**，`energy_hourly` 也从不写未结束的小时。
所以 `granularity=hour` 时，区间终点落在当前小时也**不会**返回当前小时的桶：

| 请求 | 之前 | 现在 |
| :--- | :--- | :--- |
| `from=2026-10-09 00:00&to=2026-10-09 15:00`（当时 15:18） | 16 个桶，末桶 `10-09 15` 恒为 `missing` | 15 个桶，末桶 `10-09 14`，`quality=ok` |

⚠ 调用方**不要**因此以为区间被截断了：`from` / `to` 原样回显在 `fromHour` / `toHour`，
只是「能算出来的时段」少最后一个。前端默认区间与快捷区间已同步把终点取到已结算整点。

`granularity=day` / `month` 不受影响；`/api/energy/dashboard` 不受影响
（它的桶是「截至此刻的累计」，当前时段留空是刻意的）。

### 2) 网关「在线」判定阈值改为**随采集节奏自适应**

`/api/admin/gateways` 的 `status` 与 `/api/admin/overview` 的 `kpi.onlineGateways`
现在共用同一套判定（此前一个判 5 分钟、一个判「有过任意一条日志」，同一指标两个答案）：

| 采集节奏 | 阈值 |
| :--- | :--- |
| `hourly`（现场默认） | **90 分钟**（1 个采样周期 + 30 分钟宽限） |
| `minute` | `max(5 分钟, 3 × interval_seconds)` |
| 显式配置 | `collect.gateway_offline_minutes`（分钟，优先于上表） |

⚠ 现场节奏是每小时一次，心跳间隔天然就是 60 分钟 —— 按分钟级直觉设 5 分钟会让
网关在约 92% 的时间里显示「离线」（而计量点位因判的是 `last_ok` 仍是「在线」）。

## 〇·补三、v1.4.0 新增接口与参数升级 ★ 后台数据视图必读

### 1) `from` / `to` 升级为**小时精度**（影响 3 个接口）

`/api/energy/range`、`/api/energy/dashboard`、`/api/admin/readings` 的时间端点统一为：

| 写法 | 含义 |
| :--- | :--- |
| `2026-09-24` | 当日 `00:00` |
| `2026-09-24 08` / `2026-09-24T08` | 该整点 |
| `2026-09-24 08:37` | 规范到 `08:00`（本项目最小时间桶 = 1 小时） |

⚠ 格式无法解析时接口返回 400 并写明期望格式，**不静默回退到「今天」** ——
静默回退会让用户以为筛选生效了，实际看的是另一段时间。
小时端点只在 `granularity=hour` 时生效；`day` / `month` 的桶是整天/整月，端点规范到周期边界。

### 2) `GET /api/energy/dashboard` ★ 数据中心主数据

参数：`from` / `to`（小时精度，缺省今天 00:00 → 当前整点）、`granularity`（`hour` | `day`）、`line`（可选）。

```json
{
  "from": "2026-09-24 00:00", "to": "2026-09-24 15:00",
  "granularity": "hour", "labels": ["09-24 00:00", "..."], "bucketCount": 16,
  "energies": {
    "electric": { "unit": "kWh", "values": [305.9, null], "cumulative": [305.9, 305.9],
                  "cumulativeCovered": [1, 1], "outputCumulative": [null], "unitCumulative": [null],
                  "perLineAvailable": true },
    "water": { "unit": "t", "...": "..." }, "steam": { "unit": "t", "...": "..." }
  },
  "cost": { "available": false, "values": [null], "cumulative": [null],
            "perCase": [null], "perCaseCumulative": [null],
            "standardPerCase": null,
            "blockers": ["成本参数未标定：电价、工业水价", "产量数据源未接入，折不出箱数"] },
  "productionAvailable": false,
  "totals": { "kwh": 4194.1, "waterT": null, "steamT": 2.22, "output": null,
              "costYuan": null, "perCaseCost": null }
}
```

**读法（三条都不能含糊）**

- `cumulative` 是**跳过缺数格的部分和**，`cumulativeCovered[i]` 说「累加到第 i 格吃进了几格可信数据」。
  界面必须显示覆盖度（`已覆盖 N/M 个时段`），否则中断过采集的区间会被当成完整区间。
- `cost.available=false` 时 `values / perCase / perCaseCumulative` 全为 `null`，界面**必须把
  `cost.blockers` 摊开显示**。单价或箱规任一项未标定 → 费用为 `null`，**绝不按 0 计入**
  （少算一项会得到偏小但看起来正常的费用，与 B-302 / B-505 同类）。
- `standardPerCase` 为 `null` = 未标定，**不画基准虚线**；画成 0 等于宣称「标准是 0 元/箱」。
- 选 `line=A1` 时 `energies.water/steam` 的 `perLineAvailable=false` 且序列全 `null`：
  水与汽只有车间总表。界面据此**禁用勾选**，不要画一条全零线（零会被读成「这条线没用过水」）。

### 3) `GET/PUT /api/admin/params` 系统参数（成本算料的唯一真值来源）

```json
{ "list": [ { "id": 3, "key": "price.electric", "name": "电价", "value": 0.65,
              "valueText": "0.65", "unit": "元/kWh", "group": "成本参数",
              "remark": "综合电价", "updatedAt": "...", "updatedBy": "admin" } ],
  "total": 5, "page": 1, "pageSize": 20 }
```

`PUT /api/admin/params/{id}` body `{"value": 0.72, "remark": "..."}`；
`value: null` 表示**主动清空 = 未标定**（与填 0 含义不同）。参数键不可改（键是代码取值凭据），
新增键走 `python manage.py seed_params`。默认登记五项：
`price.electric` / `price.water` / `price.steam` / `package.bottles_per_case` / `cost.standard_per_case`。

### 4) `GET /api/admin/readings` 仪表原始读数查询

参数：`from` / `to`（小时精度）、`gateway`（id 或编码）、`meter`（id 或编码）、
`line`、`energy`（`ELECTRIC`/`WATER`/`STEAM`）、`ok`（`1` 只看成功 / `0` 只看失败）、
`page` / `pageSize`（默认 50，上限 500）。

```json
{ "list": [ { "id": 1, "collectTime": "2026-09-24 14:04:04", "meter": "E-A4-01",
              "meterName": "四线 生产线电表", "gateway": "一期配电房塔石", "gatewayId": 1,
              "line": "A4", "lineName": "四线", "energy": "ELECTRIC", "energyName": "电",
              "reading": 635929.0, "unit": "kWh", "ok": true,
              "raw": "slave=11 addr=1000 regs=0061 08fa", "error": "" } ],
  "total": 98, "page": 1, "pageSize": 50, "from": "...", "to": "..." }
```

与 `/api/energy/hourly` 的分工：那边是**差分后的用量**，这边是**读数本身 + 原始报文**，
排查倍率 / 字序 / 断讯从这页开始。`reading` 为 `null` = 这次没读到（不是 0；
0 会被差值当成真实读数，B-502 的源头就在这张表）。

---

## 〇·补四、v1.4.1 大屏与后台**逐格对齐** ★ 数字核对必读

现场反馈「大屏和后台对不上」，逐格比对后修掉四处，其中三处落在接口上：

### 1) 快照新增 `ongoingHour` / `dayClosed`，`currentHour` 语义收窄

`GET /api/energy/overview` 的 `workshop` 增加两个字段：

| 字段 | 含义 | 查今天 | 查历史日（`?date=2026-09-27`） |
| :--- | :--- | :--- | :--- |
| `currentHour` | **排序锚点**：小时表从这一格往上排、备注写「已完成 N 点」 | 当前小时 | 固定 23 |
| `ongoingHour` | **真正还没结束的小时**；「进行中」状态只能由它推出 | 当前小时 | `null` |
| `dayClosed` | 查看的是已经过完的一天 | 字段不出现 | `true` |

⚠ 历史日的第 23 小时**绝不能**再走「进行中」的实时窗口（B-522）：
接口为表达「整天都结束了」把 as-of 造成那天 23:59:59，因此
`day == now.date()` 在历史日恒真 —— 谁用它判「今天」，谁就把 23 点当进行中。
实时窗口一度还只写了时间下界，结果一格装下从那天 23:04 到现在的全部用量
（实测一线 85 kWh / 真值 8.1，三线 3285 / 真值 343.1），日累计一起虚高。
现在 `hourly_cells(..., closed=True)` 让 24 小时全部按聚合表结算。

同类：查历史日时水/汽的 `utility.{water,steam}.rateNow` 由 `0.0` 改为 **`null`**
（0 的含义是「此刻没有流量」，与「不知道」相反）。前端必须走空值分支。

### 2) 电量小数口径统一为**一律 1 位小数**

`round_kwh()` 不再对 ≥20 kWh 取整。旧规则让大屏写「25」、后台写「24.8」，
几乎每行都差 0.x，属于**接口精度不一致**（B-524）：同一份数据跨界面展示时，
精度属于契约，不属于版面。前端对应新增 `dec1()`（整数不带尾随 `.0`）。

| 值 | 旧 | 新 |
| :--- | :--- | :--- |
| 7.8 | 7.8 | 7.8 |
| 24.8 | **25** | 24.8 |
| 3285.0 | 3285 | 3285 |

### 3) 周期汇总窗口 = **昨天 + 今天**（否则大屏的日/月/年永远偏小）

`collect --aggregate`（以及 `collect_loop`）统一调 `summary.refresh_rollups(now)`。
昨天 23 点的差值要等**今天 00:04** 的跨日边界读数才算得出来，那一刻
`now.date()` 已经是今天，只刷今天的日汇总就会让每一天永远少最后一个小时
（实测 09-25/26/27 的 `covered_count` 恒为 23、日合计 377.4 对不上小时表的 385.5），
而月 / 年按日行累加，三个尺度一起偏小 —— 大屏读汇总表、后台读小时表，就永远差一格（B-523）。

**验收口径（唯一能同时兜住这三条的检查）**：对任意一天，
`GET /api/energy/overview?date=D` 里每条产线 24 小时的 `kwh`，
必须与 `GET /api/energy/range?granularity=hour&scope=all&from=D 00&to=D 23`
对应线别序列**逐格相等**（含小数位），日累计同样相等。v1.4.1 已把这个断言写成单元测试
（`ClosedDaySnapshotTests.test_大屏与后台逐格相等`）。

---

## 〇·补五、v1.4.2 周期取数规则：当期现算、往期读汇总表 ★ 数字核对必读

日 / 月 / 年汇总从「每小时重刷」改为**每天 00:30 结算一次**（`summarize --rollup`，
计划任务 `EEP-Rollup`）。配套一条取数规则，写在一个地方、两个界面共用：

> **`summary.is_open_period(dim, key, now)` —— 包含「今天」的那个周期不算结束，
> 一律直接从 `energy_hourly` 现算；已结束的周期才读 `energy_daily` / `monthly` / `yearly`。**

| 界面上的位置 | 数据来源 | 更新时机 |
| :--- | :--- | :--- |
| 今日累计、当前小时 | 小时表 + 实时读数 | 每个整点 |
| 近 30 日的**今天那一根**、本月 / 本年累计 | 小时表现算（不查汇总表） | 每个整点 |
| 近 30 日的**昨天那一根** | 汇总表行**完整**时读表；行缺失或 `covered_count < expected_count` 时**也退回现算** | 每个整点（B-528） |
| 近 30 日的历史各根、近 12 月中已结束的各根 | 汇总表 | 每天 00:30 |
| 后台「用量查询」按天 / 按月 | 同一函数 `period_rows()` → 与大屏一格不差 | 同上 |

三条推论，排查时用得上：

1. **汇总表当天的行不可信，也不该被读**（它记的是 00:30 那一刻的状态）。
   有人手工把今天的日行改成 999，大屏与后台的当期数字**都不会变** —— 这是设计如此，
   不是缓存问题（单测 `OpenPeriodRollupTests` 锁的就是这一条）。
2. **`EEP-Rollup` 停掉不会报错**，只会让「近 30 日 / 近 12 月」停在最后一次结算那天，
   当期数字照常在动 —— 症状非常像「只有旧数据坏了」。巡检看
   `backend/logs/rollup-task.log` 最后一行的日期。
3. 采集侧（`collect --aggregate` / `collect_loop`）**不再刷汇总**；需要时显式加
   `collect ... --summarize`，或直接跑 `summarize`。
4. 「今天 / 昨天」以外的周期**不做现算回退** —— 回退一次要扫小时表，把它限在两个周期内，
   `daily30` 这类长序列的成本才不会涨。副作用是好的：`EEP-Rollup` 忘记注册或跑失败时，
   最近两天的数字仍然正确，只是多扫一次小时表；再早的日子才会停在最后一次结算。

---

## 一、通用约定

### 1.1 基础信息

| 项 | 约定 |
| :--- | :--- |
| Base URL | `/api`（与前端同域，由 Nginx 反向代理到后端） |
| 协议 | 内网 HTTP（如需 HTTPS 见部署文档；注意 HTTPS 页面无法加载 HTTP 视频流） |
| 编码 | UTF-8 |
| 时间格式 | ISO 8601 带时区，如 `2026-09-22T09:58:48+08:00`；日期时间字符串为 `YYYY-MM-DD HH:mm:ss` |
| 数值格式 | JSON number（不用字符串）；金额/电量保留至接口约定的小数位 |

### 1.2 鉴权

```
Authorization: Bearer <token>
```

- **所有接口必须鉴权**，白名单接口需单独评审通过后方可放行（《项目规范》5.2）。
- 正式环境走**公司统一身份认证（SSO）**，不允许独立账号体系。
- Token 失效统一返回 `401`，前端会自动跳转登录页。

### 1.3 响应格式

**不使用 `{code, data, message}` 信封**，直接返回业务数据；错误用 HTTP 状态码 + body。

成功（200）：

```json
{ "now": "...", "workshop": { } }
```

失败：

```json
{ "detail": "接口返回结构不符合约定：缺少 workshop 字段" }
```

| 状态码 | 含义 | 前端行为 |
| :--- | :--- | :--- |
| 200 | 成功 | 正常渲染 |
| 400 | 参数错误 | 提示具体字段 |
| 401 | 未登录 / Token 失效 | 跳转登录页 |
| 403 | 无权限 | 提示无权限 |
| 404 | 资源不存在 | 提示不存在 |
| 500 | 服务端异常 | 提示稍后重试，**保留上一帧画面不闪屏** |

### 1.4 分页

请求参数：

| 参数 | 类型 | 默认 | 说明 |
| :--- | :--- | :--- | :--- |
| `page` | int | 1 | 页码，从 1 开始 |
| `pageSize` | int | 10 | 每页条数 |
| `keyword` | string | — | 关键词模糊检索 |

统一响应结构：

```json
{ "list": [], "total": 128, "page": 1, "pageSize": 10 }
```

### 1.5 ⚠ 最重要的一条：「未采集」不等于「0」

这是本系统最容易出错、且后果最严重的地方。

| 情况 | 正确返回 | 错误返回 | 说明 |
| :--- | :--- | :--- | :--- |
| 该时段还没到 | `output: null, future: true` | `output: 0` | 前端渲染虚线计划柱 |
| 该时段应报未报（电表失联） | `kwh: null` | `kwh: 0` | 前端标红「电表缺失」并计入完整率 |
| 确实是计划停机 | `output: 0, stopped: true` | `output: null` | 停机与缺数据是两件事，不能混 |

**补 0 会让大屏显示成「产量暴跌到 0」，属于造假曲线，比缺数据更糟。**

### 1.6 指标口径（后端计算，前端只展示）

| 指标 | 公式 | 注意 |
| :--- | :--- | :--- |
| 单位电耗 kWh/千瓶 | `小时电量 ÷ 小时产量 × 1000` | 产量为 0 时返回 `null`，不是 0 |
| 日达成率 % | `今日累计产量 ÷ 截至当前小时的应产 × 100` | 分母用 `planElapsed`，**不是全天计划** |
| 环比 % | `(本期 − 上期) ÷ 上期 × 100` | 上期为 0 或 null 时返回 `null` |
| 车间合计 | 各线求和 | 水/汽为车间总表，不做线别拆分 |

---

## 二、大屏接口

### 2.1 `GET /api/energy/overview`

一次返回大屏总览所需的**全部**数据（含 6 条产线、趋势、告警）。

**响应：** `EnergySnapshot`（完整定义见 `types.ts`）

```jsonc
{
  "now": "2026-09-22T09:58:48+08:00",
  "workshop": {
    "currentHour": 9,

    "lines": [
      {
        "id": "A1",
        "name": "A1 生产线",
        "rated": 30000,                    // 额定产能 瓶/小时
        "product": "冰红茶 500ml",
        "status": "running",               // running | slow | stopped
        "note": "",                        // 如「CIP 清洗停机」
        "dayOfYear": 265,

        "hours": [                         // ⚠ 长度固定 24，下标 = 小时
          {
            "hour": 0,
            "output": 26141,               // 瓶；未到时段为 null
            "kwh": 197,                    // 刚过整点可能带 1 位小数
            "unitKwh": 7.54,               // 产量为 0 时为 null
            "stopped": false,
            "future": false,               // true = 未到时段
            "partial": false,              // true = 正在进行中的小时
            "projected": 26141,            // 仅 partial 时有值：整点预期产量
            "progress": 0.5                // 仅 partial 时有值：已过时间占比
          }
        ],

        "today":     { "output": 199200, "kwh": 1558, "unitKwh": 7.78,
                       "plan": 192100, "planFull": 480600, "rate": 103.7 },
        "yesterday": { "output": 194500, "kwh": 1520, "unitKwh": 7.82,
                       "fullOutput": 468000, "fullKwh": 3660 },

        "daily30":   [{ "label": "8/23", "output": 527580, "kwh": 4068, "unitKwh": 7.71 }],
        "heat":      [[2614, 2601, 2588]],  // 近 7 日 × 24 小时，单位「百瓶」，未上报为 null
        "heatDays":  ["9/16", "9/17", "9/18", "9/19", "9/20", "9/21", "9/22 今日"],

        "month": { "output": 18945100, "kwh": 147500 },
        "year":  { "output": 137930000, "kwh": 1078000,
                   "full": 190600000, "fullKwh": 1490000 }
      }
      // …A2—A6 同构
    ],

    "today":     { "output": 835736, "kwh": 6863, "unitKwh": 8.21,
                   "plan": 810000, "waterT": 2340.1, "steamT": 175.5 },
    "yesterday": { "output": 777297, "kwh": 6455, "unitKwh": 8.30,
                   "waterT": 2176,   "steamT": 163.2 },

    "month": { "output": 47871200, "kwh": 385000 },
    "year":  { "output": 601830000, "kwh": 4809000 },

    "hours":     [{ "hour": 0, "output": 76732, "kwh": 652,
                    "unitKwh": 8.50, "future": false, "partial": false }],
    "daily30":   [{ "label": "8/23", "output": 2183000, "kwh": 16900, "unitKwh": 7.74 }],
    "monthly12": [{ "label": "10月",  "output": 73180000, "kwh": 588000, "unitKwh": 8.03 }],
    "yearly5":   [{ "label": "2022年", "output": 818000000, "kwh": 6540000 }],

    "utility": {                          // 水 / 汽：只有车间总表
      "water": { "today": 2340.1, "yesterday": 2176, "rateNow": 286.4,
                 "unit": "t", "trend": [null, null, 268.2] },   // 24 个 t/h
      "steam": { "today": 175.5,  "yesterday": 163.2, "rateNow": 21.5,
                 "unit": "t", "trend": [null, null, 20.1] }
    },

    "targetUnitKwh": 8.20,
    "alerts": [
      { "level": "warn", "text": "A4 线本小时预计产量 16.6 千瓶，低于额定 75%", "time": "09:00:00" }
    ]
  }
}
```

**字段语义补充：**

| 字段 | 说明 |
| :--- | :--- |
| `currentHour` | 当前小时 0—23。车间级与产线级的 `hours[currentHour]` 即「进行中」的那个小时 |
| `hours[i].partial` | `true` 时该小时未走完，`output`/`kwh` 为**已过时间的累计值**，`projected` 为整点预期值 |
| `hours[i].future` | `true` 时表示还没到，三个数值字段一律 `null` |
| `today.plan` | **截至当前小时**的应产（日达成率分母），不是全天计划 |
| `today.planFull` | 全天计划（供预测/展示） |
| `line.status` | `slow` 的判定用 `projected` 而非已缩放的 `output`，否则整点时刻会误判 |
| `heat` | 单位「百瓶」以压缩体积；`null` 表示该时段未上报 |
| `alerts[].level` | `err` 严重 / `warn` 警告 / `info` 提示；后端已按严重度排序 |

> **实现提醒（后端最容易踩的两个坑）**
> 1. 当前小时必须用**同一比例**同时缩放产量与电量。若产量按已过分钟缩放、电量按固定比例，
>    整点刚过时 `电量/产量` 会虚高到约 20 kWh/千瓶，导致全线误报。
> 2. 电量小于 20 kWh 时保留 1 位小数。整数取整会带来 ±0.5 kWh 误差，
>    除以小产量后放大成明显的单位电耗跳动。
>
> 参考实现（含全部口径修正）：`frontend/packages/shared/src/mock/energy.ts`

### 2.2 `GET /api/energy/line/{lineId}`

单条产线的聚合数据，用于单线详情页刷新（大屏当前从 overview 取，拆成独立接口可减流量）。

**路径参数：** `lineId` — 产线编号，如 `A1`

**响应：**

```jsonc
{ "now": "2026-09-22T09:58:48+08:00", "line": { /* 同 overview 中 lines[i] 的结构 */ } }
```

### 2.4 `GET /api/energy/range` ★ 按时间段查询用量（v1.2.2 新增）

大屏与后台**共用这一个接口**，保证两边数字同源。

| 参数 | 取值 | 说明 |
| :--- | :--- | :--- |
| `from` / `to` | `YYYY-MM-DD` | 日期区间，缺省为今天；最长 366 天 |
| `granularity` | `hour` / `day` / `month` | 默认 `day` |
| `scope` | `workshop` / `line` / `all` | 默认 `all`（车间合计 + 各线一次返回） |
| `line` | 如 `A1` | `scope=line` 时必填 |

```json
{
  "from": "2026-09-17", "to": "2026-09-23",
  "granularity": "day", "scope": "all",
  "labels": ["09-17", "09-18", "..."],
  "series": [
    { "key": "workshop", "name": "车间合计", "unit": "kWh", "values": [null, 434.9, null] },
    { "key": "A1", "name": "一线", "unit": "kWh", "values": [null, null, null] }
  ],
  "quality": ["missing", "ok", "missing"],
  "utility": { "water": [null], "steam": [0.69] },
  "totals": { "kwh": 434.9, "output": 0, "waterT": null, "steamT": 0.69 },
  "productionAvailable": false
}
```

**读法上要注意的：**

- `values` 里的 `null` = **该时段缺数**（并非 0）。某小时里只要有一块电表算不出可信差值，
  该小时合计就是 `null` —— 与小时接口同一条铁律，不返回偏小的值。
- `quality` 与 `labels` 一一对应：`ok` / `partial`（部分不可信）/ `missing`（缺数）。
  界面必须把 `missing` 段明确标出来，不能当 0 画。
- `productionAvailable = false` 时 `totals.output` 恒为 0：**产量侧尚未接入**，
  界面必须显示「产量未接入」，否则「区间产量 0」会被读成「这段时间没生产」。

数据来源按粒度分：
`hour` 读 `eep_hourly_usage`（小时差值真值表）；
`day` / `month` 读 `eep_period_summary`（与后台日汇总、大屏日月年 KPI 同源）。

### 2.3 `GET /api/energy/hourly`

原始小时明细，用于导出与对账。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `date` | string | 否 | `YYYY-MM-DD`，默认今天 |
| `lineId` | string | 否 | 不传则返回全部产线 |

**响应：** 明细数组

```jsonc
[
  { "lineId": "A1", "hour": 9, "output": 26141, "kwh": 197,
    "unitKwh": 7.54, "stopped": false, "reported": true }
]
```

---

## 三、认证接口

### 3.1 `POST /api/auth/login`

> ⚠ 正式环境应替换为 SSO 回调流程（`GET /api/auth/sso/callback`），
> 本节接口仅保留给前端框架联调与极端降级场景。

**请求：**

```json
{ "username": "admin", "password": "admin123", "remember": true }
```

**响应：**

```json
{
  "token": "eyJhbGciOi...",
  "user": {
    "username": "admin",
    "nickname": "超级管理员",
    "roleName": "超级管理员",
    "avatarText": "A"
  }
}
```

### 3.2 `POST /api/auth/logout`

**响应：** `204 No Content`（前端清本地 Token）

---

## 四、后台管理接口

> 所有列表接口均支持 §1.4 的分页参数与 `keyword` 检索，响应统一为 `PageResult<T>`。

### 4.1 首页看板

#### `GET /api/admin/overview`

**响应：** `AdminOverview`

```jsonc
{
  "kpi": {
    "todayOutput": 835736, "todayKwh": 6863, "unitKwh": 8.21,
    "activeAlarms": 3, "onlineGateways": 4, "totalGateways": 5,
    "dataQualityRate": 97.2
  },
  "hourly":      [{ "label": "09:00", "output": 76732, "kwh": 652, "unitKwh": 8.50 }],
  "daily30":     [{ "label": "8/23", "output": 2183000, "kwh": 16900, "unitKwh": 7.74 }],
  "monthly12":   [{ "label": "10月", "output": 73180000, "kwh": 588000, "unitKwh": 8.03 }],
  "lineUnitKwh": [{ "lineCode": "A1", "lineName": "A1 生产线", "unitKwh": 7.78, "output": 199200 }],
  "latestAlarms": [ /* AlarmRecord[]，见 4.4 */ ]
}
```

### 4.2 系统管理

| 方法 | 路径 | 响应元素类型 |
| :--- | :--- | :--- |
| GET | `/api/admin/users` | `SysUser` |
| GET | `/api/admin/roles` | `SysRole` |
| GET | `/api/admin/menus` | `SysMenu[]`（不分页，树形） |
| GET | `/api/admin/params` | `SysParam` |
| GET | `/api/admin/dicts` | `DictItem` |

**`SysUser` 字段：**

```jsonc
{
  "id": 1, "username": "zhangwei", "nickname": "张伟",
  "deptName": "灌装一车间", "roleName": "超级管理员",
  "phone": "13800138000", "email": "zhangwei@plant01.local",
  "status": "enabled",                       // enabled | disabled
  "lastLoginAt": "2026-09-22 08:31:00",
  "createdAt": "2026-01-11 09:20:11"
}
```

**`SysParam` 字段**（能耗阈值的唯一真值来源）：

```jsonc
{
  "id": 1, "key": "energy.target.unitKwh", "name": "单位电耗考核目标",
  "value": "8.20", "unit": "kWh/千瓶", "group": "能耗阈值",
  "remark": "车间合计口径，低于该值为达标"
}
```

### 4.3 生产与设备

| 方法 | 路径 | 响应元素类型 |
| :--- | :--- | :--- |
| GET | `/api/admin/lines` | `LineMaster` |
| GET | `/api/admin/meters` | `MeterPoint` |
| POST | `/api/admin/meters/{id}/test` | `MeterTestResult`（试采，**会更新**点位最近读数） |
| POST | `/api/admin/meters/{id}/current` | `MeterCurrentResult`（读取当前值，**只读不落库**，见 〇·补五） |
| GET | `/api/admin/gateways` | `Gateway[]`（不分页） |
| GET | `/api/admin/quality` | `DataQualityRow[]`（不分页） |

**`MeterPoint` 字段**（注意 `factor` 变比系数，填错会导致该点位用量整体偏移）：

```jsonc
{
  "id": 1, "code": "EM-A1-1001", "name": "A1 生产线 配电柜电表",
  "category": "电",                          // 电 | 水 | 汽 | 气
  "lineCode": "A1",
  "protocol": "Modbus TCP",                  // Modbus TCP | Modbus RTU | OPC UA | MQTT | DL/T645
  "address": "10.20.31.11:502",
  "unit": "kWh", "factor": 1,
  "status": "online",                        // online | offline | fault
  "lastAt": "2026-09-22 09:58:00"
}
```

**`DataQualityRow` 字段**（大屏数值可信度的体检表）：

```jsonc
{
  "lineCode": "A1", "lineName": "A1 生产线",
  "expectPoints": 24, "receivedPoints": 24, "missingPoints": 0,
  "rate": 100.0, "missingHours": "—",
  "lastAt": "2026-09-22 09:58:00"
}
```

### 4.4 告警

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/admin/alarms` | 支持 `status`（active/acked/resolved）与 `level`（critical/major/minor/info）筛选 |
| POST | `/api/admin/alarms/{id}/ack` | 确认告警，**必须写操作审计日志** |

**`AlarmRecord` 字段：**

```jsonc
{
  "id": 1, "code": "ALM-20260922-0001",
  "level": "critical",                       // critical | major | minor | info
  "source": "A4 生产线",
  "content": "单位电耗超告警上限",
  "value": "10.24", "threshold": "9.60",     // 字符串，可能为「+12.4%」等非纯数值
  "status": "active",                        // active | acked | resolved
  "occurredAt": "2026-09-22 09:12:00",
  "ackedBy": "", "ackedAt": ""
}
```

### 4.5 日志

| 方法 | 路径 | 响应元素类型 |
| :--- | :--- | :--- |
| GET | `/api/admin/logs/collect` | `CollectLog` |
| GET | `/api/admin/logs/operation` | `OperationLog` |
| GET | `/api/admin/logs/login` | `LoginLog` |

**`CollectLog`**（定时任务必须记录开始/结束时间、结果、影响行数）：

```jsonc
{
  "id": 1, "taskName": "整点数据采集 09:00",
  "gateway": "灌装一车间网关", "pointCount": 62, "rows": 124,
  "durationMs": 812,
  "result": "success",                       // success | partial | failed
  "message": "全部点位入库成功",
  "startedAt": "2026-09-22 09:00:02"
}
```

### 4.6 报表

| 方法 | 路径 | 响应元素类型 |
| :--- | :--- | :--- |
| GET | `/api/admin/reports` | `ReportTemplate[]` |

---

## 五、视频监控（go2rtc，不经后端）

视频链路**不经过 Django 后端**，浏览器直连 go2rtc 网关，避免后端成为视频流量瓶颈。

| 用途 | 地址 | 谁在用 |
| :--- | :--- | :--- |
| HLS | `{gateway}/api/stream.m3u8?src={name}` | **大屏首选**（hls.js + 自有 `<video>`，可校验出画） |
| MJPEG | `{gateway}/api/stream.mjpeg?src={name}` | **大屏兜底**（`<img>` 收到真实帧才算成功） |
| MSE 播放页 | `{gateway}/stream.html?src={name}&mode=mse` | 后台预览；大屏按 V 切到 mse 链路时启用（画面不可校验） |
| WebRTC 播放页 | `{gateway}/webrtc.html?src={name}` | 同上（画面不可校验） |
| 抓图 | `{gateway}/api/frame.jpeg?src={name}` | 后台缩略图 |
| 流列表 | `{gateway}/api/streams` | 后台「检测网关」自检 |

> **注意**：视频相关接口**不属于**后端契约范围，前端不通过 Django 取流。
> 大屏默认链路为 mse → hls → mjpeg（hls 用于兜底，因为跨域 iframe 内的播放状态不可观测
> （「只有声音没有画面」无法被前端发现），详见 `docs/02-开发记录及Bug总结.md` B-207。

> 网关地址与通道映射在 `frontend/packages/shared/src/config.ts` 的 `videoConfig` 中配置。
> 若希望通过后端转发（例如统一鉴权或网关不对外暴露），需要在后端增加
> `GET /api/admin/video/gateway`（返回网关地址与流名映射）并把前端 `videoConfig.go2rtc.base`
> 指向反向代理路径。**当前实现为浏览器直连，未占用后端接口。**

---

## 六、实现检查清单

后端实现完成后，请逐项确认：

- [ ] `GET /api/energy/overview` 返回的顶层含 `workshop` 字段（前端据此判定结构是否符合约定）
- [ ] `hours` 数组长度恒为 24，下标等于小时
- [ ] 未到时段的 `output`/`kwh`/`unitKwh` 均为 `null`（不是 0），且 `future: true`
- [ ] 当前小时用同一比例缩放产量与电量
- [ ] 电量小于 20 kWh 时保留 1 位小数
- [ ] 产线状态用 `projected` 判定
- [ ] `today.plan` 是「截至当前小时」的应产
- [ ] 所有列表接口返回 `{list, total, page, pageSize}`
- [ ] 错误响应为 `{detail: "..."}`，鉴权失败返回 401
- [ ] 所有接口已鉴权，白名单接口已评审
- [ ] 告警确认等敏感操作已写审计日志
- [ ] CORS 白名单已配置（未使用 `*`）
- [ ] OpenAPI 文档已同步（`docs/openapi.yaml`）
- [ ] 已用前端 `dataConfig.mode = 'api'` 实测通过，页面无报错、数值与能源系统对得上
