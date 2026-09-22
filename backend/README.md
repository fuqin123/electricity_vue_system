# 后端（backend）—— 数据接口层

> **当前状态：预留目录，尚未实现。**
> v1.1.0 交付的是前端工程（大屏 + 后台管理系统），后端数据接口按本文档约定的契约逐步实现。

---

## 一、职责

后端是整个系统的**唯一数据入口与真值来源**，承担四件事：

1. **采集与聚合** —— 从 PLC / 智能电表 / 流量计 / 计数器拉取原始值，按整点聚合入库；
2. **口径计算** —— 单位电耗、达成率、环比等派生指标一律在后端算好再下发（避免前端各算一遍导致口径分叉）；
3. **对外接口** —— 按 `docs/api-contract.md` 向大屏与后台提供 REST 接口；
4. **鉴权与审计** —— 统一身份认证（SSO）、接口鉴权、敏感操作审计日志。

> 关键设计原则：**前端不做任何业务口径计算，只做展示**。
> 大屏与后台拿到的是同一份已算好的数据，因此不可能出现「两处数字对不上」。

---

## 二、技术栈

| 项 | 选型 | 说明 |
| :--- | :--- | :--- |
| 语言 | Python 3.10+ | 按《项目规范》3.3 统一基线 |
| 框架 | Django + Django REST Framework | 项目框架文档约定 |
| 接口文档 | OpenAPI 3.0 / Swagger | **B/S 项目必备交付物**（《项目规范》3.1） |
| 数据库 | PostgreSQL（推荐）/ MySQL | 时序数据量大时考虑按月分表或 TimescaleDB |
| 采集 | pymodbus / pyserial | Modbus TCP / RTU 读写 |
| 定时任务 | APScheduler / Celery Beat | 整点聚合、报表生成、告警判定 |
| 鉴权 | 统一身份认证（SSO） | **不允许独立账号体系**（《项目规范》3.1） |
| 依赖锁定 | pip + requirements.txt | 必须锁定版本含子依赖（《项目规范》5.4） |

---

## 三、目录规划

```text
backend/
├─ README.md                    # 本文件
├─ requirements.txt             # 依赖（版本已锁定）
├─ config/
│  └─ settings.example.yaml     # ★ 配置示例，真实配置不入库
├─ docs/
│  ├─ api-contract.md           # ★ 接口契约（前后端唯一约定，先读这个）
│  ├─ openapi.yaml              # OpenAPI 3.0 描述文件（供 Swagger UI 生成）
│  └─ data-dictionary.md        # 数据字典（待补）
├─ src/                         # Django 项目（待创建）
│  ├─ manage.py
│  ├─ config/                   # settings / urls / wsgi
│  ├─ apps/
│  │  ├─ energy/                # 能源计量（电/水/汽）
│  │  ├─ production/            # 产线与产量
│  │  ├─ collect/               # 采集任务与网关
│  │  ├─ alarm/                 # 告警规则与记录
│  │  ├─ system/                # 用户 / 角色 / 菜单 / 参数 / 字典
│  │  └─ report/                # 报表生成
│  └─ common/                   # 通用：分页、响应包装、审计、异常
└─ tests/
```

---

## 四、快速开始（待实现后补充）

```bash
cd backend

# 1. 建虚拟环境
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. 装依赖（国内镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 配置（复制示例并填真实值，真实配置不入库）
cp config/settings.example.yaml config/settings.local.yaml

# 4. 迁移与启动
cd src
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

启动后 Swagger 文档地址：`http://127.0.0.1:8000/api/docs/`

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
  mode: 'mock',                                  // ← 改为 'api' 即接后端
  api: {
    overview: '/api/energy/overview',
    line:     '/api/energy/line/{lineId}',
    hourly:   '/api/energy/hourly'
  },
  refreshMs: 30000
};
```

**只要后端返回的结构与 `api-contract.md` 一致，前端渲染代码一行都不用改。**

前端类型定义（契约的另一半）在 `frontend/packages/shared/src/types.ts`，
修改契约时**必须同时更新这两处**，否则会出现「类型对得上但字段名对不上」的隐性错配。

---

## 八、关联文档

| 文档 | 内容 |
| :--- | :--- |
| `docs/api-contract.md` | ★ 接口契约（字段级说明） |
| `docs/openapi.yaml` | OpenAPI 3.0 描述文件 |
| `../docs/01-项目介绍与环境配置.md` | 项目定位、环境、数据口径约定 |
| `../docs/02-开发记录及Bug总结.md` | 待接入功能清单、Bug 台账 |
| `../docs/04-版本部署-Linux版.md` | 后端部署（gunicorn + systemd / Docker） |
| `../docs/reference/项目规范.md` | 总部代码仓库管理规范 |
