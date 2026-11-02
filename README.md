# 能源产效管理系统（energy-efficiency-platform）

> 按《代码仓库管理规范》8.1 的 README 标准模板编写。
> 仓库建议命名：`bs-plan-energy-efficiency`（B/S · 生产规划域）

---

## 项目简介

把车间的**电、水、汽**三类能源数据与**产量**数据在同一个平台上打通：
用一块 1920×1080 大屏做车间实时监控，用一个后台管理系统做数据治理与台账管理，
让能源管理员能指着数字说清「哪条线、哪个时段、浪费在哪」。

仓库为**前后端分离**结构：`frontend/` 是前端工程（大屏 + 后台），`backend/` 是采集与数据接口
（v1.2.0 接通现场网关采集，v1.2.2 打通大屏真实数据与后台配置接口，v1.2.3 校正趋势图的时间顺序
与缺数渲染，v1.2.4 补齐产线对比面板与单线趋势图的观感，v1.3.0 数据库按业务语义重新设计，
v1.3.1 修三列底边对齐与小时状态口径，
v1.4.0 后台按区间看能源（数据中心 / 用量查询 / 计量页 / 仪表读数）并落地成本口径，仪表可在后台直接维护；
v1.4.1 让大屏与后台**逐格对得上**（历史日 23 点被当成进行中、日汇总永远少最后一小时、两边小数口径不一致、切日期时旧画面挂新日期）；
v1.4.2 周期取数按「是否还在进行」分流 —— 今天 / 本月 / 本年从小时表现算，日 / 月 / 年汇总降为每天 00:30 结算一次，新增每日计划任务 `EEP-Rollup`）；
v1.4.3 大屏跨零点自动翻页（不用重启），并给「昨天那一根」加了未结算就现算的兜底；
v1.4.4 收口后台五处显示与口径问题（侧栏图标缺失、数据中心卡片换行与两列底边不齐、
用量查询把「未结算的当前小时」算成缺数、采集网关首屏空表且一律显示离线）；
v1.5.0 仪表读数查询新增「读取当前值」—— 每行直接向现场设备要一次当前读数，
结果只弹窗展示，不写数据库、不写日志、不更新点位最近读数（并修掉试采路径未真正重试的缺陷）。

---

## 业务背景

饮料灌装产线的能耗成本集中在三块：灌装与输送的**电耗**、杀菌与 CIP 的**汽耗**、冷却与清洗的**水耗**。
原先进这三类数据分散在不同班组的表里，且只有车间总表，拿到手已经晚了 1—2 天，无法指导当班调整。

本系统把数据按**整点**收敛到统一口径，解决三个具体问题：

1. **看得见** —— 单位电耗（kWh/千瓶）随时可查，与考核目标一眼可辨；
2. **说得清** —— 低产时段、停线窗口、电表缺失自动识别为告警，不必人工翻表；
3. **算得准** —— 大屏、报表、台账共用同一套口径，不会出现三处数字对不上。

---

## 仓库结构

```text
electricity_vue_system/
├─ README.md                    # 本文件
├─ .gitignore / .gitattributes / .editorconfig / .dockerignore
│
├─ frontend/                    # ★ 前端工程（pnpm 工作区根在这里）
│  ├─ package.json              # 统一脚本入口
│  ├─ pnpm-workspace.yaml       # 工作区定义 + pnpm v10+ 设置
│  ├─ .npmrc                    # 镜像源
│  ├─ apps/
│  │  ├─ screen/                # 数据中心大屏（hash 路由，1920×1080 舞台）
│  │  └─ admin/                 # 后台管理（history 路由，Element Plus）
│  └─ packages/
│     └─ shared/                # 共享层：类型 / 配置 / Mock 引擎 / API SDK / 设计令牌
│
├─ backend/                     # ★ 后端（v1.2.2：采集 + MySQL + 能源接口 + 后台配置接口，已实测）
│  ├─ README.md                 # 后端规划与规范要求
│  ├─ requirements.txt          # 依赖（版本已锁定）
│  ├─ config/settings.example.yaml
│  └─ docs/
│     ├─ api-contract.md        # ★ 接口契约（前后端唯一约定）
│     └─ openapi.yaml           # OpenAPI 3.0（21 个接口 / 23 个 schema）
│
├─ deploy/                      # ★ 部署资源（唯一真值来源）
│  ├─ go2rtc/                   # 视频转流网关配置与排障
│  ├─ nginx/eep.conf            # Linux 站点配置
│  ├─ windows/                  # IIS web.config + NSSM 服务脚本
│  ├─ linux/                    # 一键部署 / systemd / logrotate / 巡检脚本
│  └─ docker/                   # Dockerfile + compose + 容器内 Nginx
│
└─ docs/                        # 交付文档集
   ├─ 01-项目介绍与环境配置.md
   ├─ 02-开发记录及Bug总结.md
   ├─ 03-版本部署-Windows版.md
   ├─ 04-版本部署-Linux版.md
   ├─ 05-运维手册.md
   └─ reference/                # 原始规范归档
      ├─ 项目规范.md
      └─ 项目框架及技术栈.md
```

**为什么前端工作区根在 `frontend/` 而不是仓库根**：后端是 Python（Django），
与 Node 工具链无关。把 `package.json` / `pnpm-workspace.yaml` / 锁文件收进 `frontend/`，
`backend/` 保持纯净，两边互不污染。

---

## 技术栈

| 应用 | 技术 | 版本 |
| :--- | :--- | :--- |
| 大屏 `frontend/apps/screen` | Vue / Vite / ECharts / Pinia / Vue Router(hash) / VueUse / SCSS | Vue 3.5 · Vite 6 · ECharts 5 |
| 后台 `frontend/apps/admin` | Vue / Vite / **Element Plus** / ECharts / Pinia / Vue Router(history) / VueUse / SCSS | Element Plus 2.9 |
| 共享层 `frontend/packages/shared` | TypeScript 类型 · 全局配置 · Mock 引擎 · API SDK · 设计令牌 | TypeScript 5.7 |
| 视频 | **go2rtc** 转流网关（RTSP → 浏览器可播协议；大屏走 HLS → MJPEG 可校验链路） | 现场已验证 |
| 后端（v1.2.2） | Django 5.1 + MySQL 8 + pymodbus（Modbus RTU over TCP）+ PyMySQL | Python 3.10+ |
| 工具链 | pnpm workspace · vue-tsc · Prettier · Sass | Node ≥ 20.19 · pnpm ≥ 9 |

**视觉基准**

- 大屏：深靛蓝画布 + 蓝紫/科技青/电光绿/品红和弦，1920×1080 固定舞台等比缩放
- 后台：源力设计系统 —— 主色源力蓝 `#1664FF`、4px 栅格、48px 顶栏、32px 操作高度、**仅亮色模式**

---

## 快速开始

### 环境依赖

| 组件 | 版本 | 校验 |
| :--- | :--- | :--- |
| Node.js | ≥ 20.19（推荐 22 LTS） | `node -v` |
| pnpm | ≥ 9 | `pnpm -v` |

```bash
# 若未安装 pnpm
corepack enable && corepack prepare pnpm@latest --activate
```

### 启动

```bash
cd backend
python manage.py runserver 0.0.0.0:8000 # 数据采集服务

cd frontend                 # ← 注意：工作区根在 frontend/ 下
pnpm install                # 首次约 3—6 分钟

pnpm dev:screen             # 大屏  → http://localhost:5173
pnpm dev:admin              # 后台  → http://localhost:5174（默认账号 admin / admin123）
```

### 构建

```bash
pnpm build                  # 全量构建（串行，约 25 秒）
pnpm build:screen           # 只构建大屏 → frontend/apps/screen/dist
pnpm build:admin            # 只构建后台 → frontend/apps/admin/dist
pnpm typecheck              # 全部工作区类型校验
pnpm format                 # Prettier 格式化
```

> ⚠ **`pnpm build` 是串行执行两个应用的，不要改成并行。**
> `vue-tsc` 单进程峰值内存可达 1.5GB，两个应用并行会触发换页，
> 构建时间从 25 秒涨到 10 分钟以上。

### 大屏快捷键

`1` 总览 · `2` 单线详情 · `F` 全屏 · `R` 立即刷新

---

## 视频监控（go2rtc）

**浏览器不能直接播放 `rtsp://`**，必须由网关转协议。
本项目采用 go2rtc（现场已在 `E:\能源产效管理平台\go2rtc_win64` 验证通过）。

```
摄像头(RTSP) → go2rtc 网关(:1984) → 浏览器
                                     大屏：mse → hls → mjpeg 自动降级（mse 首选，hls 可自证兜底）
                                     后台预览：mse（内置播放页 iframe）
```

> ⚠ iframe 类协议（mse / webrtc）由网关内置播放页承载，**跨域导致前端无法校验有没有画面**，
> 所以链路里必须紧跟一个能自证的 hls 作兜底（详见 `docs/02` 的 B-207 / ADR-008）。
> 大屏上按 **V** 键可循环切换链路做现场排查。
>
> 注：网关未安装 `ffmpeg` 时，MJPEG 与「抓图」不可用（HLS / MSE 不受影响），
> 见 `deploy/go2rtc/README.md`。

- 网关配置：`deploy/go2rtc/go2rtc.example.yaml`
- 部署与排障：`deploy/go2rtc/README.md`
- 前端配置：`frontend/packages/shared/src/config.ts` → `videoConfig`

> 视频链路**不经过 Django 后端**，浏览器直连网关，避免后端成为视频流量瓶颈。

---

## 当前状态

| 项 | 状态 |
| :--- | :--- |
| 版本 | `v1.7.2` |
| 前端 · 大屏 | ✅ 完成（Vue 3.5 + Vite，视觉与原设计一致） |
| 前端 · 后台 | ✅ 完成（21 个页面骨架，路由/菜单/跳转打通） |
| 浏览器视频监控 | ✅ 完成（go2rtc，四协议自动降级） |
| 后端采集 | ✅ 13 路电表 + 蒸汽总表已接通，MySQL 存原始读数与小时差值（现场实测 14/14 点位 1.4 秒） |
| 后端接口 | 🟡 能源只读接口已实现（`/api/energy/*`）+ 后台配置接口（`/api/admin/*`）；**产量如实返回 null + `available=false`**（不是 501，不是 0）；系统管理类接口待开发 |
| 时间口径 | 生产日 **06:00—次日 06:00**（v1.6.0）；日 / 月 / 年同口径，`EEP-Rollup` 06:30 结算 |
| 能源配置管理 | ✅ v1.7.0 独立菜单：标准单耗配置（产线 + 瓶型容量，电/水/汽 CRUD）+ 能源单价（电价/水价/汽价） |
| 数据质量 | ✅ v1.7.1 端点偏差可配置；partial 数据不丢失；每日复核告警（默认 11:00） |
| 标准单耗 | ✅ 按「产线 + 瓶型容量」维护；0 / 未配置显示 `-`，不参与目标线 |
| 数据源 | ✅ 大屏与后台走真实接口（`dataConfig.mode = 'api'`）：电量/汽量为采集数据；产量未接入，界面显示 `--` |
| 业务函数 | ⬜ 写操作统一给出「待接入」提示，无假死 |

**接入真实接口只需两步**（前端渲染代码一行不用改）：

1. `frontend/packages/shared/src/config.ts` → `dataConfig.mode` 改为 `'api'`；
2. 后端按 `backend/docs/api-contract.md` 实现并返回同构数据。

---

## 部署

生产环境**不需要 Node.js**，`dist/` 是纯静态文件。

| 应用 | 产物 | 路由 | 服务端要求 |
| :--- | :--- | :--- | :--- |
| 大屏 | `frontend/apps/screen/dist/` | hash | 无 rewrite 要求 |
| 后台 | `frontend/apps/admin/dist/` | history | **必须配置 try_files 回退到 index.html** |

| 场景 | 用哪些文件 | 参考文档 |
| :--- | :--- | :--- |
| Linux + Nginx（推荐） | `deploy/nginx/`、`deploy/linux/` | `docs/04-版本部署-Linux版.md` |
| Linux 一键部署 | `deploy/linux/deploy.sh` | 同上 |
| Docker Compose | `deploy/docker/` | 同上 |
| Windows + IIS | `deploy/windows/web.config.*` | `docs/03-版本部署-Windows版.md` |
| Windows + Nginx | `deploy/nginx/`、`deploy/windows/nssm-install.ps1` | 同上 |
| 视频网关 | `deploy/go2rtc/` | `deploy/go2rtc/README.md` |

> ⚠ 后台部署到子路径（如 `/admin/`）时，**必须先改**
> `frontend/apps/admin/vite.config.ts` 的 `base` 再构建，否则资源路径会 404。

---

## 维护人

| 角色 | 姓名 | 联系方式 |
| :--- | :--- | :--- |
| 项目负责人 / Owner | 待补充 | 待补充 |
| 前端 Maintainer | 待补充 | 待补充 |
| 后端 Maintainer | 待补充 | 待补充 |
| 数采 / 电气 Maintainer | 待补充 | 待补充 |

> 交付前请补齐；《代码仓库管理规范》要求每个仓库的 README 必须包含 Owner 名单。

---

## 关联文档

| 文档 | 内容 |
| :--- | :--- |
| `docs/01-项目介绍与环境配置.md` | 项目定位、技术选型理由、环境搭建、配置说明、数据口径约定 |
| `docs/02-开发记录及Bug总结.md` | 迭代记录、待接入功能清单、Bug 台账、踩坑总结、设计决策记录 |
| `docs/03-版本部署-Windows版.md` | IIS / Nginx for Windows 部署 |
| `docs/04-版本部署-Linux版.md` | Nginx + systemd / Docker / 一键脚本 |
| `docs/05-运维手册.md` | 巡检、监控、告警处置、故障排查、备份恢复、应急预案 |
| `backend/docs/采集说明.md` | ★ 后端采集侧施工图（现场取数、差值口径、部署、踩坑） |
| `backend/docs/api-contract.md` | ★ 后端接口契约（前后端唯一约定，含实现状态） |
| `deploy/README.md` | 部署资源索引 |
| `deploy/go2rtc/README.md` | 视频网关部署与排障 |
| `docs/reference/项目框架及技术栈.md` | 技术选型原始约定 |
| `docs/reference/项目规范.md` | 总部《代码仓库管理规范》 |

---

## 注意事项

1. **登录必须走统一身份认证（SSO）**，不允许独立账号体系。当前的本地虚拟登录仅用于前端框架联调。
2. **上线前必须用真实历史数据回填** `frontend/packages/shared/src/config.ts` 中的能耗强度参数与告警阈值。
3. **未采集的时段一律置 `null`，不要补 0**，否则大屏会显示成「产量暴跌」的造假曲线。
4. **电表是累计值，入库前必须差分**，并在采集侧加异常值过滤（换表/断讯会产生尖峰）。
5. **水、汽只有车间总表**，不可拆分到产线，折算方式是「车间合计产量 × 强度系数」。
6. **视频不经过后端**，浏览器直连 go2rtc；改网关地址只改 `videoConfig.go2rtc.base`。
7. 严禁提交生产密钥、客户数据、隐私信息；第三方依赖必须登记在册。
