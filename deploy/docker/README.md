# deploy/docker —— Docker 部署

> 一条 `docker compose up -d` 起整套：**前端(Nginx) + 后端(Django) + 采集 + 周期结算 + MySQL**。
> 目标机不需要源码、不需要 Node/Python、不需要外网（用离线包时）。
>
> 为什么要容器化：现场工控机的 Python/Node 版本经常对不上，
> 「在我机器上能跑」在这套系统上代价很高——采集口径错了，界面上的数字就全不可信。

---

## 1. 文件清单

| 文件 | 作用 |
| :--- | :--- |
| `Dockerfile` | 前端镜像：pnpm monorepo 多阶段构建 → Nginx 托管两个 dist |
| `Dockerfile.backend` | 后端镜像：Python 3.12 + gunicorn；**采集容器复用同一个镜像** |
| `entrypoint.backend.sh` | 等数据库 → 建库 → migrate → seed → exec 主进程 |
| `nginx.default.conf` | 容器内站点配置（`/screen/` `/admin/` `/api/` `/live/`） |
| `docker-compose.yml` | 编排：frontend / backend / collector / review / rollup / mysql（+ 可选 stream） |
| `.env.example` | 口令与参数模板，`cp .env.example .env` 后填 |
| `build-offline-bundle.sh` | 构建机执行：出镜像 → `docker save` → 打离线压缩包 |
| `load-and-run.sh` | 目标机执行：导镜像 → 生成 .env → 起服务 → 等健康检查 |
| `settings/` | 现场真实配置目录（`settings.local.yaml`），**不入库、不进镜像** |

镜像构建上下文统一是**仓库根目录**（`../..`），根目录的 `.dockerignore` 负责排除
`node_modules`、`venv`、`.env`、`settings.local.yaml`。

---

## 2. 最快跑起来（在有源码的机器上）

```bash
cd deploy/docker
cp .env.example .env            # 必填三项：两个数据库口令 + EEP_SECRET_KEY
mkdir -p settings
cp ../../backend/config/settings.example.yaml settings/settings.local.yaml
docker compose up -d --build
```

MySQL 首次建数据卷要 30~60 秒，期间 backend 会停在「等待数据库」，这是正常的。

`.env` 里的口令可以是随便起的强随机串；`settings/settings.local.yaml` 里的
`gateways` / `meters` 才是采集侧的真值来源（改完重跑 `seed_registry` 即可，幂等）。

---

## 3. 离线交付到其他服务器（推荐路径）

**构建机**（需要能联网拉 npm/pypi/Docker Hub）：

```bash
cd deploy/docker
cp .env.example .env            # build 阶段 compose 也要校验必填项
./build-offline-bundle.sh
# 产物：dist/eep-docker-bundle-v1.7.2-<日期>.tar.gz
# Docker Hub 不通时加加速前缀（只影响 mysql 这类公共基础镜像）：
#   REGISTRY_MIRROR=docker.m.daocloud.io ./build-offline-bundle.sh
```

**目标机**（只要装了 docker + compose 插件，无需外网）：

```bash
scp dist/eep-docker-bundle-*.tar.gz user@server:/opt/
ssh user@server
cd /opt && tar xzf eep-docker-bundle-*.tar.gz
cd eep-docker-bundle-*/runtime && ./load-and-run.sh
```

脚本会自己做：`docker load` → 生成 `.env`（随机强口令，权限 600）→
检查现场配置 → `docker compose up -d --no-build` → 轮询健康检查后再报访问地址。

**上线前必须改**：`runtime/settings/settings.local.yaml` 里的蒸汽平台
`params.cid` / `params.token` 和进水总表地址（示例里是 `CHANGE_ME`）。
不改的话蒸汽那一路采不到数，其余电表走串口服务器，不受影响。

---

## 4. 部署后验证清单

按顺序打，每一环都能单独定位问题：

```bash
# ① 六个容器都在、健康检查通过（frontend/backend/collector/review/rollup/mysql）
docker compose ps

# ② 前端本身活着（nginx 的自检口，不依赖后端）
curl -i http://127.0.0.1/healthz                     # 期望 200 ok

# ③ 后台产物前缀正确 —— 这一项专门验 --base=/admin/ 有没有生效
curl -s http://127.0.0.1/admin/ | grep -o '/admin/assets/[^"]*\.js' | head -1
#    期望打印出 /admin/assets/index-xxxx.js；
#    如果是 /assets/... 就说明构建时没带 base，后台必然白屏

# ④ history 路由回退（本项目最常见的部署事故）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/admin/system/user
#    期望 200；404 = try_files 回退没生效

# ⑤ 后端接口经 nginx 反代是否打得通
curl -s http://127.0.0.1/api/energy/overview | head -c 200
#    期望 {"ok":true,...}；400 = Host 不在白名单（改 EEP_ALLOWED_HOSTS）
#                        502 = 后端容器没起来（docker compose logs backend）

# ⑥ 采集是否在跑（现场最需要看这个）
docker compose logs --tail=30 collector

# ⑧ 告警是否对应真实问题（v1.5.4 起有告警表）
docker compose exec backend python manage.py check_alerts
curl -s http://127.0.0.1:8080/api/admin/alarms?status=active | head -c 200
#    每条都应能对应到现场的实事（未接入 / 网关不通 / 昨日没结算）

# ⑦ 周期结算是否跑过（漏了它，大屏「近 30 日 / 近 12 月」会静默停在最后一次结算那天）
docker compose logs --tail=6 rollup
#    期望看到「滚动窗口结算完成…：N 条」与「[rollup] 距下次结算还有 N 分钟」（生产日 06:00 结束后，06:30 结算）
```

---

## 5. 日常运维

```bash
docker compose logs -f backend          # 接口日志
docker compose logs -f collector        # 每轮采集结果
docker compose restart backend          # 只重启后端
docker compose stop collector           # 暂停采集（页面与接口不受影响）
docker compose pull mysql && docker compose up -d mysql    # 升级数据库（先备份！）

# 只更新前端（改了页面代码）
docker compose build frontend && docker compose up -d frontend

# 数据都在命名卷里，down 不会删；连数据一起清是**危险操作**：
docker compose down          # 停容器，保留数据卷
docker volume rm eep_mysql-data   # ← 真要把库删掉才执行（名字以 docker volume ls 为准）
```

# v1.6.0 生产日口径升级：迁移后必须重算一次汇总（源小时数据不动）
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py rebuild_summaries --dry-run   # 先看范围
docker compose exec backend python manage.py rebuild_summaries

备份只有一个要紧的东西 —— 数据库：

```bash
docker exec eep-mysql sh -c 'exec mysqldump --single-transaction --no-tablespaces \
  -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" eep_energy' > eep-$(date +%F).sql
```

---

## 6. 换成现场已有的 MySQL

现场已经有真实数据的库时，**不要**再让容器起一个空 MySQL：

1. `.env` 里把数据库指向那边：
   ```
   EEP_DB_HOST=10.112.66.xx
   EEP_DB_PORT=3306
   EEP_DB_NAME=eep_energy
   EEP_DB_USER=...
   EEP_DB_PASSWORD=...
   ```
2. 起服务时不启动 mysql 服务：
   ```bash
   docker compose up -d --no-build frontend backend collector
   ```
   （compose 里 `backend`/`collector` 有 `depends_on: mysql: service_healthy`，
   单独起这三个服务时 compose 不会去碰 mysql。）
3. 库不存在且账号有 `CREATE` 权限时，entrypoint 会自动建库（utf8mb4）；
   没权限就报一行明确的中文提示，让你先建库。
4. 老库升级：`docker compose exec backend python manage.py migrate`。

⚠ 用外部 MySQL 时，`--default-time-zone=+08:00` 这条就不成立了。
本系统 `USE_TZ=False`，时间字段按**本地朴素时间**读写，所以那台 MySQL 所在
主机的系统时区必须是 Asia/Shanghai，否则界面时间会整体偏 8 小时。

---

## 7. 排障对照表

| 现象 | 原因 | 处理 |
| :--- | :--- | :--- |
| 前端容器一直 restarting | 旧配置用 `upstream{}` 静态块，nginx 启动时就解析 `backend`，后端不在 → `[emerg] host not found in upstream` | 现已改为 `resolver` + 变量的运行时解析；自己改过配置就 `docker compose logs frontend` 看第一行 |
| 后台白屏、控制台报 `/assets/xxx.js` 404 | 构建没带 `--base=/admin/` | 用本目录的 `Dockerfile` 重新 `build frontend`（见验证清单 ③） |
| 后台首页能开、刷新子路由 404 | `try_files ... /admin/index.html` 丢了 | 补 `nginx.default.conf` 里那行并重建镜像 |
| 页面正常、所有接口 400 | Host 不在 `ALLOWED_HOSTS`（用 IP 访问时最常见） | 设 `EEP_ALLOWED_HOSTS`（`*` 或写死 IP/域名）后 `docker compose up -d backend` |
| 接口 502 | 后端没起来 / 连不上库 | `docker compose logs --tail=50 backend` |
| 后端反复重启，日志 `RuntimeError: 'cryptography' package is required for sha256_password or caching_sha2_password auth methods` | 连的 MySQL 8 账号用了 `caching_sha2_password`，PyMySQL 需要 `cryptography` 做 RSA 密钥交换 | 给 `requirements.txt` 补 `cryptography`；或把该账号改成 native：`ALTER USER 'eep'@'%' IDENTIFIED WITH mysql_native_password BY '…'` |
| 前端起不来，`failed to bind host port 0.0.0.0:80/tcp: address already in use` | 机器上已有 nginx / apache 占着 80 | 改 `.env` 的 `EEP_HTTP_PORT`（如 8080）或停掉占用服务；`load-and-run.sh` 已会提前拦下并打印这两条出路 |
| `settings.local.yaml 是目录` | 宿主机上没有该文件，Docker 建了同名空目录 | 按提示 `cp` 一份真实配置；compose 里已用 `create_host_path: false` 提前拦住 |
| 大屏时间比现场晚/早 8 小时 | 容器时区不是东八区，而 `USE_TZ=False` 直接取进程本地时间 | 三个应用容器与 MySQL 都要 `TZ=Asia/Shanghai`（本目录已配好） |
| `pnpm install` 卡在写 store 目录 | 仓库配置里的 `storeDir: E:\...` 是 Windows 路径 | `Dockerfile` 里已 `sed` 成容器内路径；自定义构建别漏这一步 |
| 大屏「近 30 日 / 近 12 月」一直不更新，但当天数字正常 | `rollup` 服务没跑或被停了 —— 日/月/年汇总**不**跟着采集刷（v1.4.2 起） | `docker compose up -d rollup`，再 `docker compose exec rollup python manage.py summarize --rollup` 手工补一次 |
| 构建时 `host not found in upstream`（`nginx -t`） | 站点配置写错了 | 这是**故意的**：配置错误应当在 build 阶段失败，而不是容器起来后反复重启 |

---

## 8. 改配置要同步的地方

容器内 `nginx.default.conf` 与宿主机版 `deploy/nginx/eep.conf` 是**两份文件**，
差异只在静态资源根目录（`/usr/share/nginx/html/*` vs `/opt/eep/*`）和上游解析方式。
改路由、改缓存策略、加 `location` 时两处都要改，否则会出现
「容器里能跑、宿主机部署 404」。

后端镜像不含 `settings.local.yaml`（`.dockerignore` 已排除），
所以密钥永远只存在于宿主机的挂载点上，不会进镜像层、也不会随离线包泄露出去。
