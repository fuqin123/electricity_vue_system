# 能源产效管理系统 · 版本部署（Linux 版）

> 文档版本：v1.1.0 ｜ 最后更新：2026-09-22 ｜ 适用：CentOS 7+ / Rocky Linux 8+ / Ubuntu 20.04+ / 统信 UOS / 麒麟
>
> 本文覆盖三种部署形态：
> **方案 A —— Nginx + systemd**（推荐，生产标准做法）
> **方案 B —— Docker Compose**（适合容器化环境 / 快速交付 / 离线镜像包）
> **方案 C —— 一键部署脚本**（适合现场实施人员，最少手工操作）

> ## ⚠ 配置文件的唯一来源是 `deploy/`
>
> 从 v1.1.0 起，所有可直接使用的配置与脚本已从本文档中抽出，落到 `deploy/` 目录：
>
> | 用途 | 文件 |
> | :--- | :--- |
> | Nginx 站点配置 | `deploy/nginx/eep.conf` |
> | 一键部署 / 更新 / 回滚 | `deploy/linux/deploy.sh` |
> | 后端 systemd 单元 | `deploy/linux/eep-backend.service` |
> | 日志轮转 | `deploy/linux/logrotate.eep` |
> | 每日巡检脚本 | `deploy/linux/daily-check.sh` |
> | Docker 构建与编排 | `deploy/docker/{Dockerfile,docker-compose.yml,nginx.default.conf}` |
> | 视频网关配置 | `deploy/go2rtc/go2rtc.example.yaml` |
>
> **改配置改 `deploy/` 下的文件**，本文档中的代码块仅作讲解与示意，不保证与文件同步。
> 部署资源总览见 `deploy/README.md`。

---

## 一、部署前必读

### 1.1 两个应用的部署形态不同（关键）

| 应用 | 路由模式 | 目录 | 服务端要求 |
| :--- | :--- | :--- | :--- |
| 大屏 | `hash` | `/opt/eep/screen` | **无需** rewrite |
| 后台 | `history` | `/opt/eep/admin` | **必须** `try_files` 回退到 `index.html` |

> 后台不配回退的表现：首页能开，**刷新任意子路由就 404**。这是本项目最常见的部署事故。

### 1.2 生产环境不需要 Node.js

`dist/` 是纯静态文件。推荐**在开发机构建、把产物传到服务器**；
如果 CI/CD 在服务器侧跑，才需要装 Node 22 + pnpm。

### 1.3 端口规划

| 端口 | 用途 |
| :--- | :--- |
| `80` / `443` | Nginx 站点入口 |
| `8000` | Django 后端（仅监听 `127.0.0.1`） |
| `1984` | 视频网关 go2rtc（HTTP API + 播放页 + MSE/HLS/MJPEG） |
| `8555` | 视频网关 go2rtc 的 WebRTC（可选） |

---

## 二、构建产物

在开发机（或构建机）执行：

```bash
cd /path/to/electricity_vue_system
pnpm install
pnpm build

# 打包
tar -czf screen-dist.tar.gz -C frontend/apps/screen/dist .
tar -czf admin-dist.tar.gz  -C frontend/apps/admin/dist  .
```

> 后台若部署在子路径（如 `/admin/`），**必须先改** `frontend/apps/admin/vite.config.ts` 的 `base: '/admin/'`
> 再构建。大屏不用改（已是 `base: './'`）。

上传到服务器：

```bash
scp screen-dist.tar.gz admin-dist.tar.gz root@10.20.31.100:/tmp/
```

---

## 三、方案 A：Nginx + systemd（推荐）

### 3.1 安装 Nginx

```bash
# Rocky / CentOS / 麒麟
sudo dnf install -y nginx

# Ubuntu / Debian / 统信 UOS
sudo apt update && sudo apt install -y nginx

# 验证
nginx -v
```

如果是**内网离线环境**，用本地 RPM/DEB 包安装：

```bash
# CentOS 系
sudo rpm -ivh nginx-*.rpm
# 缺依赖时
sudo yum install -y --disablerepo=* --nogpgcheck ./*.rpm
```

### 3.2 创建目录并部署文件

```bash
sudo mkdir -p /opt/eep/screen /opt/eep/admin /opt/eep/_backup

sudo tar -xzf /tmp/screen-dist.tar.gz -C /opt/eep/screen
sudo tar -xzf /tmp/admin-dist.tar.gz  -C /opt/eep/admin

# 属主改为 nginx 运行用户，避免权限告警
sudo chown -R nginx:nginx /opt/eep      # Debian/Ubuntu 上用户是 www-data
sudo chmod -R 755 /opt/eep

# 确认产物到位
ls -l /opt/eep/screen /opt/eep/admin
```

### 3.3 配置 Nginx

新建 `/etc/nginx/conf.d/eep.conf`：

```nginx
# ══════════════════════════════════════════════════════════════
# 能源产效管理系统 · Nginx 站点配置
# 大屏：hash 路由，无需 rewrite
# 后台：history 路由，必须 try_files 回退
# ══════════════════════════════════════════════════════════════

# 后端 Django（同机部署，只监听回环，避免被直接访问）
upstream eep_backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

# 视频网关 go2rtc
upstream eep_stream {
    server 127.0.0.1:1984;
    keepalive 16;
}

server {
    listen       80;
    listen       [::]:80;
    server_name  _;

    charset      utf-8;
    root         /opt/eep;
    index        index.html;

    # 上传限制（报表导入等）
    client_max_body_size 20m;

    access_log   /var/log/nginx/eep-access.log  main;
    error_log    /var/log/nginx/eep-error.log   warn;

    # ── 安全响应头 ─────────────────────────────────────────
    add_header X-Content-Type-Options  "nosniff"        always;
    add_header X-Frame-Options         "SAMEORIGIN"     always;
    add_header Referrer-Policy         "same-origin"    always;
    server_tokens off;

    # ── gzip 压缩（大屏 JS 约 1 MB，收益显著）───────────────
    gzip              on;
    gzip_vary         on;
    gzip_comp_level   6;
    gzip_min_length   1024;
    gzip_proxied      any;
    gzip_types        text/plain text/css application/javascript application/json
                      application/xml image/svg+xml font/woff2;

    # ── 大屏（hash 路由）──────────────────────────────────
    location /screen/ {
        alias /opt/eep/screen/;
        try_files $uri $uri/ /screen/index.html;

        # 带内容哈希的产物文件名 → 可长期强缓存
        location ~* ^/screen/assets/.*\.(js|css|woff2?|ttf|png|jpe?g|svg|ico)$ {
            expires 365d;
            add_header Cache-Control "public, immutable";
            access_log off;
        }

        # 入口文件绝不缓存，保证发版即生效
        location = /screen/index.html {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            expires -1;
        }
    }

    location = /screen { return 301 /screen/; }

    # ── 后台管理（history 路由）────────────────────────────
    location /admin/ {
        alias /opt/eep/admin/;

        # ★ 关键：未命中的路径回退到 index.html，否则刷新子路由 404
        try_files $uri $uri/ /admin/index.html;

        location ~* ^/admin/assets/.*\.(js|css|woff2?|ttf|png|jpe?g|svg|ico)$ {
            expires 365d;
            add_header Cache-Control "public, immutable";
            access_log off;
        }

        location = /admin/index.html {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            expires -1;
        }
    }

    location = /admin { return 301 /admin/; }

    # ── 后端 API 反向代理 ─────────────────────────────────
    location /api/ {
        proxy_pass          http://eep_backend;
        proxy_http_version  1.1;
        proxy_set_header    Host              $host;
        proxy_set_header    X-Real-IP         $remote_addr;
        proxy_set_header    X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header    X-Forwarded-Proto $scheme;
        proxy_set_header    Connection        "";

        proxy_connect_timeout  10s;
        proxy_send_timeout     120s;
        proxy_read_timeout     120s;

        # 后端接口一律不缓存
        add_header Cache-Control "no-store";
    }

    # ── 视频转流（FLV / HLS 长连接）────────────────────────
    location /live/ {
        proxy_pass          http://eep_stream/;
        proxy_http_version  1.1;
        proxy_set_header    Host $host;
        proxy_set_header    X-Real-IP $remote_addr;

        # ⚠ FLV 是长连接：必须关闭缓冲并放宽读超时，否则画面几秒就断
        proxy_buffering          off;
        proxy_cache              off;
        proxy_read_timeout       3600s;
        proxy_send_timeout       3600s;
        chunked_transfer_encoding off;

        # 允许大屏页面跨域拉流
        add_header Access-Control-Allow-Origin  * always;
        add_header Access-Control-Allow-Methods "GET, OPTIONS" always;
    }

    # ── 根路径跳后台 ──────────────────────────────────────
    location = / { return 302 /admin/; }

    # 健康检查（供监控系统探测）
    location = /healthz {
        access_log off;
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }

    # 屏蔽敏感文件
    location ~ /\.(?!well-known) { deny all; }
    location ~* \.(tar|tar\.gz|zip|sql|bak|log)$ { deny all; }
}
```

> **`alias` 的尾斜杠很重要**：`location /admin/ { alias /opt/eep/admin/; }` ——
> 两边都要有尾斜杠，否则路径会拼错（变成 `/opt/eep/admin` + `/admin/xxx`）。

启用并校验：

```bash
sudo ln -sf /etc/nginx/conf.d/eep.conf /etc/nginx/conf.d/eep.conf

sudo nginx -t
# 期望输出：
#   nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
#   nginx: configuration file /etc/nginx/nginx.conf test is successful

sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

### 3.4 SELinux 注意事项（CentOS / Rocky / 麒麟）

如果 SELinux 是 Enforcing 状态，Nginx 会**无权读取 `/opt` 下的文件**，表现为 403。

**推荐做法 —— 给目录打正确的 SELinux 标签**（比直接关 SELinux 安全）：

```bash
# 查状态
getenforce

# 给新目录打标签（永久生效）
sudo semanage fcontext -a -t httpd_sys_content_t "/opt/eep(/.*)?"
sudo restorecon -Rv /opt/eep

# 允许 Nginx 发起反向代理连接（proxy_pass 必需）
sudo setsebool -P httpd_can_network_connect 1

# 若日志目录也需要写
sudo semanage fcontext -a -t httpd_log_t "/var/log/nginx(/.*)?"
sudo restorecon -Rv /var/log/nginx
```

> `semanage` 命令来自 `policycoreutils-python-utils` 包：
> `sudo dnf install -y policycoreutils-python-utils`

**临时验证用**（不推荐长期）：

```bash
sudo setenforce 0     # 临时切到 Permissive
```

### 3.5 防火墙放行

```bash
# firewalld（CentOS / Rocky / 麒麟）
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
sudo firewall-cmd --list-all

# 只允许内网网段（更严格）
sudo firewall-cmd --permanent --zone=internal --add-source=10.20.0.0/16
sudo firewall-cmd --permanent --zone=internal --add-service=http
sudo firewall-cmd --reload

# ufw（Ubuntu / Debian / UOS）
sudo ufw allow from 10.20.0.0/16 to any port 80 proto tcp
sudo ufw enable
sudo ufw status verbose
```

### 3.6 后端 Django 服务化（systemd）

后端部署在 `/opt/eep-backend`，用 gunicorn + systemd 常驻：

```ini
# /etc/systemd/system/eep-backend.service
[Unit]
Description=EEP Django Backend (gunicorn)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=notify
User=eep
Group=eep
WorkingDirectory=/opt/eep-backend

# 环境变量从文件读取（避免密钥写进 unit 文件）
EnvironmentFile=-/opt/eep-backend/.env

# gunicorn：仅监听回环，外部访问统一走 Nginx
ExecStart=/opt/eep-backend/.venv/bin/gunicorn \
          --workers 3 \
          --threads 4 \
          --bind 127.0.0.1:8000 \
          --timeout 120 \
          --access-logfile /var/log/eep/backend-access.log \
          --error-logfile  /var/log/eep/backend-error.log \
          --capture-output \
          config.wsgi:application

ExecReload=/bin/kill -s HUP $MAINPID

Restart=always
RestartSec=5

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/eep-backend /var/log/eep

# 日志（同时交给 journald，便于 journalctl 排查）
StandardOutput=journal
StandardError=journal
SyslogIdentifier=eep-backend

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /sbin/nologin eep
sudo mkdir -p /var/log/eep && sudo chown eep:eep /var/log/eep

sudo systemctl daemon-reload
sudo systemctl enable --now eep-backend
sudo systemctl status eep-backend

# 跟随日志
sudo journalctl -u eep-backend -f --since "10 min ago"
```

### 3.7 日志轮转

```ini
# /etc/logrotate.d/eep
/var/log/eep/*.log {
    daily
    rotate 30
    size 50M
    compress
    delaycompress
    missingok
    notifempty
    create 0640 eep eep
    sharedscripts
    postrotate
        systemctl reload eep-backend > /dev/null 2>&1 || true
    endscript
}
```

```bash
# 手动验证配置（不实际执行）
sudo logrotate -d /etc/logrotate.d/eep

# 强制立即轮转一次
sudo logrotate -f /etc/logrotate.d/eep
```

> 按《代码仓库管理规范》第 5.3 条：**本地日志至少保留 30 天，且按大小滚动**。
> 上面的配置已同时满足（`rotate 30` + `size 50M`）。

### 3.8 验证清单

```bash
# 1. Nginx 配置与状态
sudo nginx -t && systemctl is-active nginx

# 2. 静态文件可访问
curl -sI http://127.0.0.1/screen/ | head -3
curl -sI http://127.0.0.1/admin/  | head -3

# 3. ★ 关键：history 回退是否生效（应返回 200 而不是 404）
curl -s -o /dev/null -w "子路由刷新 HTTP 状态: %{http_code}\n" http://127.0.0.1/admin/system/user

# 4. API 代理是否通
curl -s http://127.0.0.1/api/health || echo "后端未就绪"

# 5. 健康检查
curl -s http://127.0.0.1/healthz
```

逐项人工验证：

- [ ] 浏览器打开 `http://<服务器IP>/` → 自动跳后台登录页
- [ ] `http://<服务器IP>/screen/` 大屏正常，图表渲染完整
- [ ] 大屏按 `2` 进单线详情正常
- [ ] 后台登录成功，**21 个菜单逐个点击**均能打开
- [ ] **刷新 `/admin/system/user` 不 404** ← 最关键
- [ ] 视频网关配好后大屏出图；关掉网关降级为占位而非白屏

---

## 四、方案 B：Docker Compose

适用场景：容器化环境、多环境一致性要求高、需要快速交付（离线镜像包）。

> **配置的真值在 `deploy/docker/`，本节只讲结构与踩过的坑，不复制文件内容。**
> 完整步骤、部署后验证清单、排障对照表见 `deploy/docker/README.md`。

### 4.1 服务组成

| 服务 | 镜像 | 职责 |
| :--- | :--- | :--- |
| `frontend` | `Dockerfile` 构建（Nginx） | 托管大屏 + 后台静态产物，反代 `/api/`、`/live/` |
| `backend` | `Dockerfile.backend` 构建（gunicorn） | Django 接口；启动时自动 migrate + seed |
| `collector` | **与 backend 同一个镜像** | 每 5 分钟 `collect --aggregate --if-due` |
| `review` | **与 backend 同一个镜像** | 每分钟检查 `data_quality.review_hour`，生成异常时段人工复核告警 |
| `rollup` | **与 backend 同一个镜像** | 每天 06:30 `summarize --rollup`，结算生产日 / 月 / 年 |
| `mysql` | `mysql:8.0` | 业务库；换现场已有库见 docker/README 第 6 节 |
| `stream` | `alexxit/go2rtc` | 可选，`--profile stream` 才启动 |

采集与接口共用镜像是刻意的：两边必须跑同一份代码，否则「接口读到的」和
「采集写入的」口径会分叉，而这种分叉在界面上看起来像数据错了。

### 4.2 容器化踩到的五个坑

1. **Nginx 不能用 `upstream{}` 静态块指向后端容器**
   nginx 在**启动瞬间**解析 upstream 里的主机名。后端容器没起来就是
   `[emerg] host not found in upstream "backend"` → 前端容器 crash loop，
   现场看到的现象是「整个系统打不开」，而真实故障只在后端。
   必须用 `resolver 127.0.0.11` + 变量形式的 `proxy_pass`，把解析推迟到请求时；
   后端不在也只是 `/api` 返回 502，静态页面照常可访问。

2. **后台产物必须带 `--base=/admin/` 构建**
   源码里的 `base: '/'` 是给「后台部署在站点根目录」那种形态用的；容器里放在
   `/admin/` 子路径，不带 base 构建出的 `index.html` 引用 `/assets/xxx.js`
   → 后台整片白屏。在 Dockerfile 里用 CLI 覆盖，**不改源码**
   （理由见 `deploy/README.md` 红线 2）。

3. **采集容器要等 `backend` healthy，不是等 `mysql` healthy**
   建表是 backend 的 entrypoint 做的。只等 mysql，采集第一轮就撞
   `(1146, "Table 'eep_energy.energy_meter' doesn't exist")`。

4. **MySQL 8 的 `caching_sha2_password` 需要 `cryptography` 包**
   PyMySQL 走这条认证要做 RSA 密钥交换，而 `requirements.txt` 里没有该包
   （新增依赖按《项目规范》需评审）。所以容器内的库显式用
   `--default-authentication-plugin=mysql_native_password`。
   ⚠ 改成连现场已有的 MySQL 时，若那边账号是 caching_sha2_password，
   就必须给 `requirements.txt` 补一行 `cryptography`，否则同样的错误会再现。

5. **周期结算必须单独排一个服务**
   v1.4.2 起 `collect` 默认**不**再刷日/月/年汇总（`--summarize` 要显式加），
   改由每天 06:30 的 `summarize --rollup` 结算（生产日 06:00 结束）。漏掉它的现象是**静默的**：
   当天数字照常从小时表现算、不报错，而大屏「近 30 日 / 近 12 月」永远停在
   最后一次结算那天。依据见 `backend/scripts/daily-rollup-task.py` 顶部说明。

另有两条与「数据正确性」直接相关，不只是跑不跑得起来：

- **时区**：`settings.py` 里 `USE_TZ=False`，时间按本地朴素时间读写。
  三个应用容器与 MySQL 都要 `Asia/Shanghai`（MySQL 还要
  `--default-time-zone=+08:00`），否则界面时间整体偏 8 小时，
  而这套系统的核心口径就是「每小时差值」——错位等于数据全废。
- **不要把 apt/apk 装包写进 Dockerfile**：内网构建机上 `deb.debian.org:80`
  经常不通，而 `python:3.12-slim`、`nginx:1.27-alpine` 本来就自带 zoneinfo
  与 curl。多那一步只是让构建在必定失败的地方失败。

### 4.3 启动与更新

```bash
cd deploy/docker
cp .env.example .env                                   # 填口令与密钥
mkdir -p settings
cp ../../backend/config/settings.example.yaml settings/settings.local.yaml
docker compose up -d --build                           # 首次：建库 + 迁移 + 登记设备

docker compose build frontend && docker compose up -d frontend    # 只更新前端
docker compose logs -f collector                       # 现场最该看的一路
docker compose ps                                      # 六个容器的健康状态
```

### 4.4 离线环境交付

```bash
# 构建机（需能访问 npm / pypi / Docker Hub 或加速源）
cd deploy/docker && cp .env.example .env
./build-offline-bundle.sh
# → dist/eep-docker-bundle-v1.7.2-<日期>.tar.gz（三个镜像 + runtime/）

# 目标机（无需源码、无需外网，只要装了 docker + compose v2）
tar xzf eep-docker-bundle-*.tar.gz
cd eep-docker-bundle-*/runtime && ./load-and-run.sh
```

Docker Hub 不通时给构建脚本加加速前缀：

```bash
REGISTRY_MIRROR=docker.m.daocloud.io ./build-offline-bundle.sh
```

⚠ 离线包里的 `settings/settings.local.yaml` 由 `backend/config/settings.example.yaml`
生成，蒸汽平台 token 等仍是 `CHANGE_ME`，上线前必须现场填好。

---

### 4.5 v1.6.0 升级（生产日口径）

v1.6.0 把「日」从自然日改为 **06:00—次日 06:00**，并新增 `energy_standard_unit`。升级步骤：

```bash
cd deploy/docker
docker compose pull                         # 或用离线包 import
docker compose up -d mysql                  # 先让数据库可用
docker compose run --rm backend python manage.py migrate
# ⚠ 必须执行一次：旧的自然日汇总不能继续读；源读数/小时表/产量表不受影响
docker compose run --rm backend python manage.py rebuild_summaries
docker compose up -d
docker compose logs --tail=6 rollup         # 期望末尾是 06:30 结算窗口
```

- 迁移 `0013_v160_line_codes` 会把六条产线原地改码为 W103/W101/W102/W301/W104/W105；如果库里同时存在旧码和新码，迁移会报冲突并停下，先人工核对再升级。
- 迁移 `0014_v160_standard_unit` 只建新表；标准单耗初始为空（未标定），在后台「系统管理 → 标准单耗」录入。
- `rebuild_summaries` 会清空并重建三张汇总表；请安排在维护窗口，执行前可先 `--dry-run` 看范围。

### 4.6 v1.7.0 升级（能源配置管理）

```bash
docker compose up -d
# 迁移会自动执行：0015 标准单耗增加瓶型容量、0016 归位能源单价分组
# 本版不需要重建日/月/年汇总
```

升级后后台「能源配置管理」下出现「标准单耗配置」「能源单价」两页；系统参数页不再显示三项能源单价。
迁移 0015 会为没有标准配置的产线补空白行（value=NULL），不会覆盖已有值。

### 4.7 v1.7.1 升级（异常时段复核）

```bash
docker compose up -d
# 迁移 0017 建 energy_review_log；0018 更新告警 category 选项
# 新增 review 容器（每分钟检查复核时刻）；采集/结算任务不变
```

升级后到「系统管理 → 系统参数 → 数据质量」确认：
`data_quality.review_hour`（默认 11 时）与 `data_quality.deviation_minutes`（默认 10 分钟）。
复核时刻到达后，上一生产日的 partial 小时会生成带差值和时段的 `hour_deviation` 告警；
未复核时数据仍正常显示，不需要重建汇总。

### 4.8 v1.7.2 升级（采集连接关闭）

```bash
docker compose build backend frontend
docker compose up -d
# 无数据库迁移；collector/backend 重启后生效
```

本版修复采集器在 `prepare()` 失败/异常路径未关闭 TCP client 的问题；
升级后可用 `ss -tnp | grep <网关IP>` 观察失败轮次后连接数是否回落。
若水表仍长时间无响应，请按 `docs/05` §6.2.1 检查串口服务器/设备侧与是否有第二个采集环境。

## 五、方案 C：一键部署脚本

适合现场实施人员：把「解压 + 配置 + 校验 + 启停 + 备份 + 回滚」全部固化。

### 5.1 部署脚本 `deploy.sh`

```bash
#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# 能源产效管理系统 · 一键部署脚本
# 用法：
#   ./deploy.sh install          首次安装
#   ./deploy.sh update <tar包>   更新（自动备份，失败可回滚）
#   ./deploy.sh rollback         回滚到最近一次备份
#   ./deploy.sh status           查看状态
# ══════════════════════════════════════════════════════════════
set -euo pipefail

APP_ROOT="/opt/eep"
BACKUP_DIR="${APP_ROOT}/_backup"
NGINX_CONF="/etc/nginx/conf.d/eep.conf"
KEEP_BACKUPS=5

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; NC=$'\033[0m'
info()  { echo "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo "${YELLOW}[WARN]${NC}  $*"; }
error() { echo "${RED}[ERROR]${NC} $*" >&2; }

need_root() {
  if [[ $EUID -ne 0 ]]; then
    error "请用 root 或 sudo 执行"
    exit 1
  fi
}

# ── 环境自检 ────────────────────────────────────────────────
preflight() {
  info "环境自检..."
  command -v nginx >/dev/null 2>&1 || { error "未安装 nginx，请先安装"; exit 1; }
  if command -v getenforce >/dev/null 2>&1; then
    warn "SELinux 状态：$(getenforce)（若为 Enforcing，需执行 semanage + restorecon，见部署文档 3.4）"
  fi
  local avail
  avail=$(df -m "${APP_ROOT%/*}" 2>/dev/null | awk 'NR==2{print $4}')
  if [[ -n "${avail}" && "${avail}" -lt 1024 ]]; then
    warn "磁盘可用空间不足 1GB（当前 ${avail} MB），备份可能失败"
  fi
}

# ── 首次安装 ────────────────────────────────────────────────
do_install() {
  need_root
  preflight

  local screen_tar="${1:-}" admin_tar="${2:-}"
  [[ -f "${screen_tar}" ]] || { error "大屏产物包不存在：${screen_tar}"; exit 1; }
  [[ -f "${admin_tar}"  ]] || { error "后台产物包不存在：${admin_tar}"; exit 1; }

  info "创建目录"
  mkdir -p "${APP_ROOT}/screen" "${APP_ROOT}/admin" "${BACKUP_DIR}"

  info "解压产物"
  tar -xzf "${screen_tar}" -C "${APP_ROOT}/screen"
  tar -xzf "${admin_tar}"  -C "${APP_ROOT}/admin"

  local nginx_user="nginx"
  id "${nginx_user}" >/dev/null 2>&1 || nginx_user="www-data"
  chown -R "${nginx_user}:${nginx_user}" "${APP_ROOT}"
  chmod -R 755 "${APP_ROOT}"

  info "写入 Nginx 配置"
  if [[ ! -f "${NGINX_CONF}" ]]; then
    cp "$(dirname "$0")/eep.conf" "${NGINX_CONF}"
  else
    warn "${NGINX_CONF} 已存在，跳过（如需更新请手工替换）"
  fi

  info "SELinux 标签（若启用）"
  if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
    semanage fcontext -a -t httpd_sys_content_t "${APP_ROOT}(/.*)?" 2>/dev/null || true
    restorecon -Rv "${APP_ROOT}" || true
    setsebool -P httpd_can_network_connect 1 || true
  fi

  info "校验并重载 Nginx"
  nginx -t || { error "Nginx 配置校验失败，请检查 ${NGINX_CONF}"; exit 1; }
  systemctl enable --now nginx
  systemctl reload nginx

  do_verify
}

# ── 更新（带自动备份）────────────────────────────────────────
do_update() {
  need_root

  local admin_tar="${1:-}"
  [[ -f "${admin_tar}" ]] || { error "后台产物包不存在：${admin_tar}"; exit 1; }

  local ts; ts=$(date +%Y%m%d-%H%M%S)
  mkdir -p "${BACKUP_DIR}"

  info "备份当前版本 → ${BACKUP_DIR}/admin-${ts}.tar.gz"
  if [[ -d "${APP_ROOT}/admin" && -n "$(ls -A "${APP_ROOT}/admin" 2>/dev/null)" ]]; then
    tar -czf "${BACKUP_DIR}/admin-${ts}.tar.gz" -C "${APP_ROOT}/admin" .
  else
    warn "当前 admin 目录为空，跳过备份"
  fi

  # 保留最近 N 份
  ls -1t "${BACKUP_DIR}"/admin-*.tar.gz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f

  info "替换文件"
  find "${APP_ROOT}/admin" -mindepth 1 -delete
  tar -xzf "${admin_tar}" -C "${APP_ROOT}/admin"

  local nginx_user="nginx"
  id "${nginx_user}" >/dev/null 2>&1 || nginx_user="www-data"
  chown -R "${nginx_user}:${nginx_user}" "${APP_ROOT}/admin"

  if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
    restorecon -Rv "${APP_ROOT}/admin" >/dev/null || true
  fi

  info "重载 Nginx"
  nginx -t && systemctl reload nginx

  do_verify
  info "更新完成。如需回滚：./deploy.sh rollback"
}

# ── 回滚 ───────────────────────────────────────────────────
do_rollback() {
  need_root
  local latest
  latest=$(ls -1t "${BACKUP_DIR}"/admin-*.tar.gz 2>/dev/null | head -1 || true)
  [[ -n "${latest}" ]] || { error "没有可用备份"; exit 1; }

  warn "即将回滚到：${latest}"
  read -r -p "确认继续？(yes/N) " reply
  [[ "${reply}" == "yes" ]] || { info "已取消"; exit 0; }

  find "${APP_ROOT}/admin" -mindepth 1 -delete
  tar -xzf "${latest}" -C "${APP_ROOT}/admin"

  local nginx_user="nginx"
  id "${nginx_user}" >/dev/null 2>&1 || nginx_user="www-data"
  chown -R "${nginx_user}:${nginx_user}" "${APP_ROOT}/admin"

  nginx -t && systemctl reload nginx
  info "回滚完成"
  do_verify
}

# ── 验证 ───────────────────────────────────────────────────
do_verify() {
  local host="127.0.0.1"
  info "验证..."
  sleep 1

  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://${host}/screen/" || echo "000")
  [[ "${code}" == "200" ]] && info "大屏首页        HTTP ${code} ✓" || warn "大屏首页        HTTP ${code} ✗"

  code=$(curl -s -o /dev/null -w "%{http_code}" "http://${host}/admin/" || echo "000")
  [[ "${code}" == "200" ]] && info "后台首页        HTTP ${code} ✓" || warn "后台首页        HTTP ${code} ✗"

  # 最关键的一项：history 回退
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://${host}/admin/system/user" || echo "000")
  if [[ "${code}" == "200" ]]; then
    info "后台子路由回退  HTTP ${code} ✓"
  else
    error "后台子路由回退  HTTP ${code} ✗  → 检查 try_files 配置！"
  fi

  code=$(curl -s -o /dev/null -w "%{http_code}" "http://${host}/api/health" || echo "000")
  [[ "${code}" == "200" ]] && info "后端 API        HTTP ${code} ✓" || warn "后端 API        HTTP ${code}（后端可能未启动）"
}

do_status() {
  echo "── Nginx ──"
  systemctl is-active nginx && nginx -t 2>&1 | tail -1
  echo "── 目录 ──"
  du -sh "${APP_ROOT}/screen" "${APP_ROOT}/admin" 2>/dev/null || true
  echo "── 备份 ──"
  ls -lht "${BACKUP_DIR}" 2>/dev/null | head -6 || echo "（无备份）"
}

case "${1:-}" in
  install)  shift; do_install  "${@:-}";;
  update)   shift; do_update   "${@:-}";;
  rollback) do_rollback;;
  status)   do_status;;
  *)
    cat <<'USAGE'
能源产效管理系统 · 部署工具

用法：
  ./deploy.sh install <大屏tar包> <后台tar包>   首次安装
  ./deploy.sh update  <后台tar包>               更新后台（自动备份）
  ./deploy.sh rollback                          回滚到最近一次备份
  ./deploy.sh status                            查看当前状态
USAGE
    exit 1
    ;;
esac
```

### 5.2 使用方式

```bash
sudo chmod +x deploy.sh

# 首次安装
sudo ./deploy.sh install screen-dist.tar.gz admin-dist.tar.gz

# 更新后台
sudo ./deploy.sh update admin-dist.tar.gz

# 出问题回滚
sudo ./deploy.sh rollback

# 看状态
sudo ./deploy.sh status
```

### 5.3 systemd 定时备份（可选）

```ini
# /etc/systemd/system/eep-backup.service
[Unit]
Description=EEP 每日备份

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'ts=$(date +\%Y\%m\%d); tar -czf /opt/eep/_backup/daily-$ts.tar.gz -C /opt/eep/admin . && ls -1t /opt/eep/_backup/daily-*.tar.gz | tail -n +8 | xargs -r rm -f'
```

```ini
# /etc/systemd/system/eep-backup.timer
[Unit]
Description=EEP 每日备份（每日 02:00）

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now eep-backup.timer
systemctl list-timers eep-backup.timer
```

---

## 六、HTTPS 配置（可选）

内网若已申请证书（或自建 CA）：

```nginx
server {
    listen 443 ssl;
    http2  on;
    server_name eep.plant01.local;

    ssl_certificate     /etc/nginx/ssl/eep.crt;
    ssl_certificate_key /etc/nginx/ssl/eep.key;

    ssl_protocols             TLSv1.2 TLSv1.3;
    ssl_ciphers               ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache         shared:SSL:10m;
    ssl_session_timeout       10m;

    add_header Strict-Transport-Security "max-age=31536000" always;

    # ... 与 HTTP 站点相同的 location 配置 ...
}

server {
    listen 80;
    server_name eep.plant01.local;
    return 301 https://$host$request_uri;
}
```

> **大屏特别提醒**：若大屏页面用 HTTPS 访问，而视频转流网关是 HTTP，
> 浏览器会以「混合内容」为由**拦截视频**。两个办法：
> ① 网关也上 HTTPS；② 全部走 HTTP（纯内网推荐）。
> 内网部署建议**统一用 HTTP**，避免这个坑。

---

## 七、常见问题

### Q1. 后台刷新子路由 404

`try_files $uri $uri/ /admin/index.html;` 没写对，或 `alias` 路径拼接错误。
**自检命令**：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1/admin/system/user
# 期望 200；返回 404 就是回退没生效
```

### Q2. 所有页面 403 Forbidden

三种可能：
① SELinux 标签不对（见 §3.4）→ `restorecon -Rv /opt/eep`；
② 目录权限不足 → `chown -R nginx:nginx /opt/eep && chmod -R 755 /opt/eep`；
③ Nginx 进程用户与实际目录属主不匹配 → `ps aux | grep nginx` 看运行用户。

### Q3. 大屏页面正常，但视频播不了

先确认 `videoConfig.channels[*].flv` 是否已填网关地址；
其次检查 `/live/` 代理是否关了缓冲：

```bash
curl -sI http://127.0.0.1/live/a1.flv | head -5
```

如果画面几秒后中断，基本都是 `proxy_buffering` 没关或 `proxy_read_timeout` 太短。

### Q4. 更新后页面还是旧内容

浏览器强缓存了 `index.html`。本配置已对 `index.html` 加 `no-cache`；
若仍出现，检查是否有前置 CDN 或代理缓存。用户侧强制刷新 `Ctrl + F5` 可立即验证。

### Q5. `nginx -t` 报 `duplicate location`

同一个 `server` 块里定义了两次相同 `location`。检查是否有多个 `conf.d/*.conf` 都配了 `/` 或 `/api/`。

```bash
grep -rn "location /" /etc/nginx/conf.d/ /etc/nginx/nginx.conf
```

### Q6. 大屏挂机几天后内存持续增长

浏览器侧的问题（7×24 常亮页面）。本项目已做的防护：
① 图表实例在组件卸载时 `dispose()`；② 时钟每秒只改文本节点，不重绘整屏。
若仍增长，用浏览器任务管理器观察，必要时给大屏加**每日凌晨自动刷新**（可在 kiosk 模式脚本里定时 `location.reload()`）。

---

## 八、上线检查表

### 部署前

- [ ] 后台 `vite.config.ts` 的 `base` 与部署路径一致
- [ ] `pnpm build` 零报错，`pnpm typecheck` 零报错
- [ ] 产物包已校验完整性（`tar -tzf` 能列出文件）
- [ ] 已确认服务器磁盘可用空间 > 1GB

### 部署中

- [ ] Nginx 配置通过 `nginx -t`
- [ ] SELinux 标签已处理（如启用）
- [ ] 防火墙已按内网网段放行
- [ ] 后端服务健康检查通过

### 部署后

- [ ] 大屏可访问、图表正常、快捷键生效
- [ ] 后台可登录、21 个菜单逐个点开
- [ ] **刷新后台子路由不 404**
- [ ] 视频网关连通（如已配置）
- [ ] 日志轮转生效，日志目录有写入
- [ ] 备份任务已启用（timer / cron）
- [ ] 回滚流程已实测一次

### 安全

- [ ] 已接入统一身份认证（SSO），移除本地虚拟登录
- [ ] API 全部鉴权，白名单接口已评审
- [ ] CORS 白名单已配置，未使用 `*`
- [ ] 生产密钥未写入代码库或镜像
- [ ] 第三方依赖许可证已确认（无 GPL 强传染性）
- [ ] 每月依赖 CVE 巡检已排期
