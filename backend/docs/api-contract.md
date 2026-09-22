# 接口契约（API Contract）

> 版本：v1.1.0 ｜ 最后更新：2026-09-22
>
> **本文件是前后端的唯一约定。** 前端已按本契约预留好取数层
> （`frontend/packages/shared/src/api/index.ts`），
> 只要后端返回结构与此一致，前端渲染代码**一行都不用改**。
>
> ⚠ 修改契约时必须**同时更新三处**，否则会出现隐性错配：
> ① 本文件 ② `frontend/packages/shared/src/types.ts` ③ `docs/openapi.yaml`

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
