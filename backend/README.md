# 后端（backend）—— 数据采集与接口层

> **当前状态：v1.2.1 —— 采集、存储、只读接口与后台配置接口均已实现并接现场实测通过。**
>
> | 能力 | 状态 |
> | :--- | :--- |
> | 13 路产线电表（4 台 485 串口服务器，Modbus RTU over TCP） | ✅ 实测 14/14 点位 1.4 秒采完 |
> | 车间蒸汽总表（第三方平台 API） | ✅ 已接通 |
> | 小时差值（每小时用了多少）＋ 异常过滤 | ✅ 含缺数 / 换表 / 尖峰 / 偏离整点四类处理 |
> | 日 / 月 / 年周期总计（`eep_period_summary`，大屏与后台同源） | ✅ `summarize` 落表，整点自动刷新 |
> | MySQL 存储 | ✅ 已建表入库（网关 IP/端口/从站号等仪表配置均在库，支持后台维护） |
> | 能源数据只读接口 | ✅ `/api/energy/*`（与前端契约同构） |
> | 后台配置接口 | ✅ v1.2.1 打通 `/api/admin/*`：网关 / 计量点**后台增删改 + 连接测试 + 试采**（写操作 `source=admin`，`seed_registry` 不覆盖） |
> | 产量（瓶子数） | 🟡 表已建、写入命令已备（`ingest_production`），数据源未接入期间接口如实返回 **null + available=false**（不是 501、不是 0） |
> | 车间进水总表 | ⬜ 接口地址待现场提供（配置已登记，`enabled: false`） |
> | 告警引擎 / 报表 / 权限 / SSO | ⬜ 后续版本 |
>
> **怎么跑、口径是什么、踩过哪些坑 —— 见 `docs/采集说明.md`（先读这个）。**

---

## 一、职责

后端是整个系统的**唯一数据入口与真值来源**，承担四件事：

1. **采集与聚合** —— 从智能电表 / 流量计 / PLC / 计数器拉取原始值，按整点差分成小时用量；
2. **口径计算** —— 单位电耗、达成率、环比等派生指标一律在后端算好再下发（避免前端各算一遍导致口径分叉）；
3. **对外接口** —— 按 `docs/api-contract.md` 向大屏与后台提供 REST 接口；
4. **鉴权与审计** —— 统一身份认证（SSO）、接口鉴权、敏感操作审计日志（待实现）。

> 关键设计原则：**前端不做任何业务口径计算，只做展示**。
> 大屏与后台拿到的是同一份已算好的数据，因此不可能出现「两处数字对不上」。

---

## 二、技术栈

| 项 | 选型 | 说明 |
| :--- | :--- | :--- |
| 语言 | Python 3.10+ | 按《项目规范》3.3 统一基线（现场实测用 3.13） |
| 框架 | Django 5.1 | 本次只用了 Django 核心 + DjangoJSONEncoder，未引入 DRF |
| 数据库 | **MySQL 8.0** + PyMySQL | 现场指定；PyMySQL 是纯 Python 驱动，Windows 上免编译 |
| 采集 | **pymodbus 3.7**（`FramerType.RTU`） | 串口服务器是「485 报文进 TCP」，必须用 RTU 组帧 |
| 定时任务 | 系统计划任务（schtasks / cron） | 比常驻进程抗崩溃；`collect_loop` 也提供 |
| 接口文档 | OpenAPI 3.0 | 见 `docs/openapi.yaml`（本次实现部分已在 `api-contract.md` 标注） |
| 鉴权 | 统一身份认证（SSO） | **不允许独立账号体系**（《项目规范》3.1），待接入 |
| 依赖锁定 | pip + requirements.txt | 必须锁定版本含子依赖（《项目规范》5.4） |

---

## 三、目录结构（实际）

```text
backend/
├─ manage.py                    # 命令入口
├─ requirements.txt             # 依赖（版本已锁定）
├─ config/
│  ├─ settings.example.yaml     # ★ 配置示例（含现场设备登记表），真实配置不入库
│  └─ settings.local.yaml       # ⚠ 本地真实配置（已 gitignore）
├─ eep/                         # Django 工程
│  ├─ conf.py                   # 配置加载（环境变量 > local > example）
│  ├─ settings.py               # MySQL / 时区 / CORS / 日志
│  ├─ urls.py · wsgi.py
├─ apps/energy/                 # 能源采集（核心）
│  ├─ models.py                 # Gateway / Line / Meter / Reading / HourlyUsage / HourlyProduction / PeriodSummary / CollectLog
│  ├─ collectors/               # 采集器：base（重试/计时）· modbus_rtu · http_api
│  ├─ services/                 # collect（编排）· aggregate（小时差值）· snapshot（组装接口）· summary（周期总计）· registry（登记表同步）
│  ├─ management/commands/      # collect · collect_loop · aggregate · summarize · ingest_production · seed_registry · purge_readings
│  ├─ api.py · urls.py          # 能源只读接口（/api/energy/*）+ 后台配置接口（/api/admin/*）
│  ├─ tests.py                  # 44 个单元测试（差值口径 + 序列顺序与空值 + 字序 + JSON 取值）
│  └─ migrations/
└─ docs/
   ├─ 采集说明.md                # ★ 现场取数方式、口径、部署、踩坑
   ├─ api-contract.md           # ★ 接口契约（字段级）
   └─ openapi.yaml              # OpenAPI 3.0 描述文件
```

---

## 四、快速开始（已实测）

```bash
cd backend

# 1. 装依赖（国内镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 建库（字符集必须 utf8mb4）
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS eep_energy \
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"

# 3. 配置（复制示例并填数据库口令与平台 token；真实配置不入库）
cp config/settings.example.yaml config/settings.local.yaml

# 4. 建表 + 同步设备登记表
python manage.py migrate
python manage.py seed_registry

# 5. 采一轮（14 个点位，约 1.4 秒）
python manage.py collect

# 6. 算小时差值并起服务
python manage.py aggregate --recent 2
python manage.py runserver 0.0.0.0:8000
```

自检：

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/api/energy/collect-summary
python manage.py test apps.energy      # 44 个单元测试
```

---

## 五、必须遵守的规范（摘自《项目规范》，不可协商）

### 5.1 B/S 项目必备交付物

- [x] 后端 API 必须有 OpenAPI / Swagger 文档 → `docs/openapi.yaml`
- [ ] 必须有 Dockerfile 或部署文档 → 见 `../deploy/`
- [ ] 必须配置 CORS、白名单、SQL 注入防护
- [ ] 登录必须走统一身份认证（SSO），**不允许独立账号体系**
- [ ] 所有接口必须鉴权（白名单接口除外，需评审通过）
- [ ] 敏感操作必须记录审计日志

### 5.2 编码规范

- 严格遵循 PEP 8，配置 `pre-commit` + `black` + `flake8` + `isort`
- 所有公共函数、类、模块必须有 docstring（Google 或 NumPy 风格）
- 复杂逻辑加行注释，解释「**为什么**」而非「是什么」
- `TODO` 必须写明负责人：`# TODO(张三): 待优化 (2026-Q4 处理)`
- 命名：类 `大驼峰`、函数/变量 `小写+下划线`、常量 `全大写+下划线`、私有 `前缀下划线`

### 5.3 脚本 / 定时任务额外要求

- 必须记录**开始时间、结束时间、执行结果、影响行数**
- 失败**必须有告警**（邮件 / 企业微信 / 电话）
- 长期任务必须支持**断点续跑**
- 所有外部调用（API、数据库、文件）必须有 `try/except`
- 调用外部接口必须有重试（如 `tenacity`）
- 关键操作（写库、发请求）必须**幂等**

### 5.4 配置与安全

- 数据库连接、密钥等**禁止硬编码**，用环境变量或配置文件
- 配置文件优先级：**环境变量 > 配置文件 > 默认值**
- 严禁提交生产密钥、客户数据、隐私信息
- 第三方依赖新增必须经过评审；严禁 GPL 等强传染性许可证

---

## 六、采集侧必须注意的三条（会直接影响大屏数字可信度）

1. **电表值是累计值，入库前必须差分。**
   智能电表上报的是总有功电能（只增不减）。换表、归零、通讯中断都会在差分后产生**尖峰**，
   必须在采集侧做异常值过滤（例如单小时电量超过该线额定产能对应能耗的 2 倍，即判为异常并置 `null`）。

2. **未采集到的时段必须返回 `null`，不要补 0。**
   前端按 `future: true` 渲染成虚线计划柱与灰色行。补 0 会让大屏显示成「产量暴跌到 0」，
   属于造假曲线，比缺数据更糟。

3. **水、汽只有车间总表，不可拆分到产线。**
   车间级指标按「车间合计产量 × 强度系数」折算，不要伪造线别分摊。

> 接口字段的精确含义见 `docs/api-contract.md`，其中标注了每个字段的「为 0」与「为 null」的区别。

---

## 七、与前端的关系

前端通过 `frontend/packages/shared/src/config.ts` 的 `dataConfig.mode` 切换数据源：

```ts
export const dataConfig = {
  mode: 'api',          // ← v1.2.1 起为 'api'（读真实库）；改回 'mock' 为内置演示数据
  mockOnly: [           // 后端尚未实现的域（系统管理 / 告警 / 报表等）继续走演示数据
    'auth', 'admin.users', /* … */
  ],
  api: {
    overview: '/api/energy/overview',
    line:     '/api/energy/line/{lineId}',
    hourly:   '/api/energy/hourly',
    meters:   '/api/energy/meters',
    collectSummary: '/api/energy/collect-summary'
  },
  refreshMs: 30000
};
```

⚠ 后端不可达时大屏会**降级为演示数据并显示醒目提示**（`source='mock'` + notice），
不会把演示数字当真实数据展示。

**只要后端返回的结构与 `api-contract.md` 一致，前端渲染代码一行都不用改。**

前端类型定义（契约的另一半）在 `frontend/packages/shared/src/types.ts`，
修改契约时**必须同时更新这两处**，否则会出现「类型对得上但字段名对不上」的隐性错配。

---

## 八、关联文档

| 文档 | 内容 |
| :--- | :--- |
| `docs/采集说明.md` | ★ **采集侧施工图**：现场取数方式、小时差值口径、部署运行、踩坑记录 |
| `docs/api-contract.md` | ★ 接口契约（字段级说明，含实现状态） |
| `docs/openapi.yaml` | OpenAPI 3.0 描述文件 |
| `../docs/01-项目介绍与环境配置.md` | 项目定位、环境、数据口径约定 |
| `../docs/02-开发记录及Bug总结.md` | 待接入功能清单、Bug 台账（后端 bug 记在 B-3xx 段） |
| `../docs/04-版本部署-Linux版.md` | 后端部署（gunicorn + systemd / Docker） |
| `../docs/reference/项目规范.md` | 总部代码仓库管理规范 |
